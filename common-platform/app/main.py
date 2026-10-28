from __future__ import annotations

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .conversation import ConversationService
from .agent_engine import AgentEngine
from .conversation_routes import create_conversation_router
from .interaction_routes import create_interaction_router
from .workflow_routes import create_workflow_router
from .devtools import Devtools
from .devtools_routes import create_devtools_router
from .file_routes import create_file_router, is_upload_chunk_request
from .workflow_transport import MAX_CHUNK_BYTES
from .identity_routes import create_identity_router
from .identity import refresh_actor
from .db import (
    Artifact,
    Audit,
    Conversation,
    LoginSession,
    Resource,
    Run,
    User,
    audit,
    create_database,
    resource_dict,
    run_dict,
    uid,
    user_dict,
)
from .errors import PlatformError, require
from .model_gateway import ModelGateway, ModelGatewayError
from .model_routes import create_model_router
from .module_routes import create_module_router
from .remote_services import RemoteServiceManager, RemoteServiceError
from .remote_routes import create_remote_router, remote_error_response
from .runtime import Runtime, accessible
from .workspace_contract import MAX_INVOKE_BYTES
from .security import (
    COOKIE,
    check_csrf,
    password_matches,
    require_admin,
    require_user,
    seed_users,
    session_identity,
    token_hash,
)

ROOT = Path(__file__).resolve().parent.parent


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class InvokeInput(StrictModel):
    capability_id: str = Field(max_length=160)
    input: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)
    expected_revision: int | None = None
    conversation_id: str | None = None
    origin: str = Field(default="page", pattern="^(page|conversation)$")


class MessageInput(StrictModel):
    content: str = Field(min_length=1, max_length=24000)
    model_profile_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=100)


class DecideInput(StrictModel):
    decision: str = Field(pattern="^(accept|return)$")
    note: str = Field(default="", max_length=2000)
    expected_revision: int = Field(ge=1)


class ForkInput(StrictModel):
    message_id: str | None = None


def create_app(data_dir: Path | None = None, start_worker=True, gateway=None, remote_services=None):
    data_dir = Path(data_dir or os.getenv("PLATFORM_DATA_DIR", str(ROOT / "data"))).resolve()
    engine, sessions = create_database(data_dir)
    seed_users(sessions, data_dir=data_dir)
    gateway = gateway or ModelGateway(data_dir)
    if remote_services is None:
        remote_services = RemoteServiceManager(data_dir)
        from .monitor_client import AggregateMonitorClient

        remote_services._monitor = AggregateMonitorClient(data_dir, remote_services)
    runtime = Runtime(ROOT, sessions, gateway, remote_services, data_dir=data_dir)
    from .coordination_client import CoordinationClient
    from .coordination_routes import create_coordination_router

    coordination = CoordinationClient(data_dir, remote_services)
    agent_engine = AgentEngine(runtime, data_dir)
    conversation = ConversationService(runtime, agent_engine=agent_engine)

    @asynccontextmanager
    async def lifespan(app):
        conversation.recover_incomplete()
        monitor_stop = asyncio.Event()
        monitor_task = None
        coordination_task = None

        async def coordinate_registry():
            while not monitor_stop.is_set():
                try:
                    await coordination.reconcile()
                except PlatformError:
                    pass  # The admin view reports unavailable; business events stay in module outboxes.
                try:
                    await asyncio.wait_for(monitor_stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

        if start_worker:
            await runtime.start()
            await runtime.compositions.start()
            monitor_task = asyncio.create_task(remote_services.monitor_loop(monitor_stop))
            coordination_task = asyncio.create_task(coordinate_registry())
        try:
            yield
        finally:
            monitor_stop.set()
            if monitor_task:
                await monitor_task
            if coordination_task:
                await coordination_task
            if start_worker:
                await runtime.compositions.stop()
                await runtime.stop()
            agent_engine.close()
            engine.dispose()

    app = FastAPI(
        title="通用能力平台",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.sessions, app.state.engine, app.state.runtime, app.state.gateway = (
        sessions,
        engine,
        runtime,
        gateway,
    )
    app.state.remote_services = remote_services
    from .ai_services import AIServices

    ai_services = AIServices(runtime)
    app.state.ai_services = ai_services
    app.state.agent_engine = agent_engine
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )
    login_failures = {}

    @app.middleware("http")
    async def boundary(request, call_next):
        length = request.headers.get("content-length", "0") or "0"
        if not length.isdigit():
            return JSONResponse(
                {"error": {"code": "request_size", "message": "请求长度无效。"}}, status_code=400
            )
        chunk_request = is_upload_chunk_request(request.url.path, request.method)
        body_limit = (
            MAX_CHUNK_BYTES
            if chunk_request
            else MAX_INVOKE_BYTES if request.url.path == "/api/v1/invoke" else 512_000
        )
        if int(length) > body_limit:
            return JSONResponse(
                {"error": {"code": "request_size", "message": "请求超过该接口的大小限制。"}},
                status_code=413,
            )
        if request.method in ("POST", "PUT", "PATCH") and not chunk_request:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > body_limit:
                    return JSONResponse(
                        {
                            "error": {
                                "code": "request_size",
                                "message": "请求超过该接口的大小限制。",
                            }
                        },
                        status_code=413,
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse(
                {"error": {"code": "origin", "message": "请求来源不匹配。"}}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        try:
            frame_actor = require_user(request)
        except PlatformError:
            frame_actor = None
        frame_sources = " ".join(runtime.frame_sources(frame_actor))
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' "
            + frame_sources
            + "; frame-ancestors 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        # Management uses a secret-input channel rather than recording plaintext invocation input.
        if (
            request.url.path.startswith(
                ("/api/v1/models", "/api/v1/remote-services", "/api/v1/modules", "/api/v1/ai-services", "/api/v1/coordination")
            )
            and request.method in ("POST", "PUT", "DELETE")
            and response.status_code < 400
        ):
            try:
                actor = require_user(request)
                with sessions.begin() as db:
                    if request.url.path.startswith("/api/v1/modules"):
                        category = "modules"
                    elif request.url.path.startswith("/api/v1/remote-services"):
                        category = "services"
                    elif request.url.path.startswith("/api/v1/ai-services"):
                        category = "ai-services"
                    else:
                        category = "models"
                    audit(
                        db,
                        actor["id"],
                        category + "." + request.method.lower(),
                        request.url.path,
                        status=response.status_code,
                    )
            except PlatformError:
                pass
        return response

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            status_code=exc.status,
        )

    @app.exception_handler(ModelGatewayError)
    async def model_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=400)

    @app.exception_handler(RemoteServiceError)
    async def remote_error(request, exc):
        return remote_error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "request_validation",
                    "message": "请求字段不符合要求。",
                    "details": [{"path": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
                }
            },
            status_code=422,
        )

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "service": "common-platform",
            "profile": "local",
            "version": "0.4.0",
            "registered_modules": len(runtime.registry.modules) - 1,
            "remote_services_enabled": len(remote_services.enabled_bindings()),
            "model_configured": gateway.configured,
            "execution_backend": "local-durable-sql",
            "production_infrastructure": False,
        }

    @app.post("/api/v1/auth/login")
    def login(body: LoginInput, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        history = [t for t in login_failures.get(client, []) if t > time.time() - 300]
        require(len(history) < 20, "login_rate", "登录失败过多，请稍后重试。", 429)
        with sessions.begin() as db:
            user = db.query(User).filter_by(username=body.username).first()
            if (
                not user
                or not user.enabled
                or not password_matches(body.password, user.password_hash)
            ):
                login_failures[client] = history + [time.time()]
                raise PlatformError("login_failed", "账号或密码不正确。", 401)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            old = request.cookies.get(COOKIE)
            if old:
                db.query(LoginSession).filter_by(token_hash=token_hash(old)).delete()
            db.add(
                LoginSession(
                    token_hash=token_hash(token),
                    user_id=user.id,
                    csrf=csrf,
                    expires=time.time() + 12 * 3600,
                )
            )
            audit(db, user.id, "auth.login", user.id)
            response.set_cookie(
                COOKIE,
                token,
                httponly=True,
                samesite="strict",
                max_age=12 * 3600,
                secure=request.url.scheme == "https",
            )
            return {"user": user_dict(user), "csrf_token": csrf}

    @app.get("/api/v1/auth/session")
    def who(request: Request):
        return session_identity(request)

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response, actor=Depends(check_csrf)):
        with sessions.begin() as db:
            db.query(LoginSession).filter_by(
                token_hash=token_hash(request.cookies.get(COOKIE, ""))
            ).delete()
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/v1/registry")
    def registry(actor=Depends(require_user)):
        return {**runtime.public_registry(actor), "flows": [runtime.flow]}

    @app.post("/api/v1/invoke")
    async def invoke(body: InvokeInput, actor=Depends(check_csrf)):
        payload = dict(body.input)
        if body.expected_revision is not None:
            payload.setdefault("expected_revision", body.expected_revision)
        return await runtime.invoke(
            actor,
            body.capability_id,
            payload,
            body.idempotency_key,
            body.origin,
            body.conversation_id,
        )

    @app.get("/api/v1/runs")
    def runs(actor=Depends(require_user)):
        with sessions() as db:
            actor = refresh_actor(db, actor)
            query = db.query(Run)
            if actor["role"] != "admin":
                query = query.filter_by(actor_id=actor["id"])
            return {
                "items": runtime.visible_runs(
                    db, query.order_by(Run.created_at.desc()).limit(100), actor
                )
            }

    @app.get("/api/v1/runs/{run_id}")
    def run_get(run_id: str, actor=Depends(require_user)):
        with sessions() as db:
            run = db.get(Run, run_id)
            require(run is not None, "not_found", "任务不存在。", 404)
            require(
                run.actor_id == actor["id"] or actor["role"] == "admin",
                "forbidden",
                "无权读取此任务。",
                403,
            )
            return runtime.current_run_view(db, run, actor)

    @app.post("/api/v1/runs/{run_id}/retry")
    async def retry(run_id: str, actor=Depends(check_csrf)):
        return await runtime.control_run(actor, run_id, "retry")

    @app.post("/api/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str, actor=Depends(check_csrf)):
        return await runtime.control_run(actor, run_id, "cancel")

    @app.get("/api/v1/resources")
    def resources(actor=Depends(require_user)):
        with sessions() as db:
            actor = refresh_actor(db, actor)
            query = db.query(Resource)
            if actor["role"] != "admin":
                query = query.filter_by(owner_id=actor["id"])
            items = []
            for resource in query.order_by(Resource.updated_at.desc()).limit(100):
                try:
                    accessible(resource, actor)
                except PlatformError:
                    continue
                items.append(resource_dict(db, resource))
            return {"items": items}

    @app.get("/api/v1/resources/{resource_id}")
    def resource_get(resource_id: str, actor=Depends(require_user)):
        with sessions() as db:
            actor = refresh_actor(db, actor)
            resource = db.get(Resource, resource_id)
            accessible(resource, actor)
            return resource_dict(db, resource)

    @app.post("/api/v1/resources/{resource_id}/decide")
    async def decide(
        resource_id: str, body: DecideInput, request: Request, actor=Depends(check_csrf)
    ):
        return await runtime.invoke(
            actor,
            "core.resource.decide",
            {**body.model_dump(), "resource_id": resource_id},
            request.headers.get("Idempotency-Key") or uid(),
        )

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    def download(artifact_id: str, actor=Depends(require_user)):
        with sessions.begin() as db:
            actor = refresh_actor(db, actor)
            artifact = db.get(Artifact, artifact_id)
            require(artifact is not None, "not_found", "文件不存在。", 404)
            resource = db.get(Resource, artifact.resource_id)
            accessible(resource, actor)
            audit(db, actor["id"], "artifact.download", artifact.id)
            return Response(
                artifact.content.encode("utf-8"),
                media_type=artifact.media_type,
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.name),
                    "Cache-Control": "no-store",
                },
            )

    @app.get("/api/v1/invocations/{invocation_id}/workspace/{view_index}/{block_index}/download")
    def workspace_download(
        invocation_id: str, view_index: int, block_index: int, actor=Depends(require_user)
    ):
        with sessions.begin() as db:
            content, name, media_type = runtime.workspace_download(
                db, actor, invocation_id, view_index, block_index
            )
            return Response(
                content,
                media_type=media_type,
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

    @app.get("/api/v1/audit")
    def audit_get(actor=Depends(require_user)):
        with sessions() as db:
            actor = refresh_actor(db, actor)
            return {"items": runtime.audit_data(db, actor)}

    app.state.coordination = coordination
    app.include_router(create_coordination_router(coordination, runtime, require_admin, check_csrf))
    app.include_router(create_conversation_router(conversation, require_user, check_csrf))
    app.include_router(create_interaction_router(runtime.interactions, require_user, check_csrf))
    app.include_router(create_workflow_router(runtime.workflows, require_user, check_csrf))
    app.include_router(create_devtools_router(Devtools(runtime), require_admin, check_csrf))
    from .business_center import create_business_router
    from .access_routes import create_access_router

    app.include_router(create_business_router(runtime, require_user, check_csrf))
    app.include_router(create_access_router(runtime, require_user, check_csrf))
    from .access_bridge import create_access_bridge_router

    app.include_router(create_access_bridge_router(runtime.access_bridge))
    from .source_reads import SourceReads, create_source_read_router

    app.include_router(create_source_read_router(SourceReads(runtime)))
    from .runtime_observation import create_observation_router

    app.include_router(create_observation_router(runtime.observations, require_user))
    from .atomic_instances import create_atomic_router

    app.include_router(create_atomic_router(runtime.atomic_instances, require_user, check_csrf))
    from .module_catalog import create_module_catalog_router

    app.include_router(
        create_module_catalog_router(runtime.module_catalog, require_user, check_csrf)
    )
    from .composition_routes import create_composition_router

    app.include_router(create_composition_router(runtime.compositions, require_user, check_csrf))
    app.include_router(
        create_file_router(
            runtime.workflows.transport,
            runtime.workflows.resolve_transfer,
            require_user,
            check_csrf,
        )
    )
    app.include_router(create_identity_router(sessions, require_user, check_csrf), prefix="/api/v1")
    app.include_router(
        create_model_router(gateway, require_admin, require_user, check_csrf), prefix="/api/v1"
    )
    from .ai_routes import create_ai_router

    app.include_router(create_ai_router(ai_services, require_admin, check_csrf))
    app.include_router(
        create_remote_router(remote_services, runtime, require_admin, check_csrf), prefix="/api/v1"
    )
    app.include_router(
        create_module_router(runtime, require_admin, require_user, check_csrf), prefix="/api/v1"
    )
    # Publish only authored documentation and explicit acceptance artifacts, not
    # demo source trees, deployment files, tokens or databases.
    if (ROOT / "reports").exists():
        app.mount("/reports", StaticFiles(directory=ROOT / "reports", html=True), name="reports")
    app.mount("/docs", StaticFiles(directory=ROOT / "docs"), name="project-docs")
    app.mount("/prompts", StaticFiles(directory=ROOT / "prompts"), name="developer-prompts")

    from .project_documents import create_project_documents_router

    app.include_router(create_project_documents_router(ROOT))

    dist = ROOT / "web/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def index(path: str):
        # Navigation uses URL fragments; arbitrary file-shaped paths must not
        # appear to be successful downloads of private deployment files.
        require(not path, "not_found", "页面或文件不存在。", 404)
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(
            {"message": "后端已启动，前端尚未构建。请在 web 目录运行 npm run build。"}
        )

    return app


app = create_app()
