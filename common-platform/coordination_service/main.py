"""Run this API as its own process with an independent configuration directory."""
from contextlib import asynccontextmanager
import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .protocol import Ack, Config, Decision, Event, CoordinationError, EVENT_BYTES, CONFIG_BYTES
from .store import CoordinationStore


def bearer(request):
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer ") or value != value.strip() or " " in value[7:]:
        return ""
    return value[7:]


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


async def body(request, limit):
    data = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise CoordinationError("coordination_body_size", 413)
                data.extend(chunk)
        return json.loads(data, object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise CoordinationError("coordination_body_format") from None
    except TimeoutError:
        raise CoordinationError("coordination_body_timeout", 408) from None


def create_app(directory=None, start_worker=True, clock=None, control_token=None):
    directory = directory or os.getenv("DUOFLOW_COORDINATION_DATA_DIR")
    if not directory:
        raise ValueError("Configure an independent DUOFLOW_COORDINATION_DATA_DIR")
    store = CoordinationStore(directory, clock=clock, control_token=control_token)

    @asynccontextmanager
    async def lifespan(app):
        # Deliveries are claimed by authenticated consumers. Admission, retry
        # eligibility and lease expiry are transactional on every pull; no API
        # request starts business handlers or waits on another microservice.
        yield

    app = FastAPI(title="DuoFlow 业务协同中心", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.coordination = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.exception_handler(CoordinationError)
    async def coordination_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": "请检查协同配置、权限或对象修订。"}}, status_code=exc.status)

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"error": {"code": "coordination_contract", "message": "协同字段或参数无效。"}}, status_code=422)

    @app.get("/health")
    def health():
        return {"service": "duoflow-coordination", "status": "ok", "version": "1.0", "center_id": store.center_id}

    @app.put("/api/v1/coordination/config")
    async def configure(request: Request):
        token = bearer(request)
        store.authorize(token)
        return JSONResponse(store.configure(token, Config.model_validate(await body(request, CONFIG_BYTES))), headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/coordination/config")
    def configuration(request: Request):
        return JSONResponse(store.configuration(bearer(request)), headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/coordination/events")
    async def events(request: Request):
        token = bearer(request)
        store.authorize_module(token)
        return store.accept(token, Event.model_validate(await body(request, EVENT_BYTES)))

    @app.get("/api/v1/coordination/deliveries")
    def deliveries(request: Request, consumer_id: str = "", limit: int = 20):
        return JSONResponse(store.pull(bearer(request), consumer_id, limit), headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/coordination/deliveries/{delivery_id}/ack")
    async def ack(delivery_id: str, request: Request):
        token = bearer(request)
        store.authorize_module(token)
        command = Ack.model_validate(await body(request, 1024))
        return store.ack(token, delivery_id, command.consumer_id, command.lease_token)

    @app.post("/api/v1/coordination/approvals/{event_id}/decide")
    async def decide(event_id: str, request: Request):
        token = bearer(request)
        store.authorize(token)
        return store.decide(token, event_id, Decision.model_validate(await body(request, 1024)))

    @app.get("/api/v1/coordination/summary")
    def summary(request: Request):
        return JSONResponse(store.summary(bearer(request)), headers={"Cache-Control": "no-store"})

    return app
