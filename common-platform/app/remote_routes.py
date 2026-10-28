"""Admin connection lifecycle; remote secrets never enter invocation history."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import require
from .identity import can_manage_platform, refresh_actor
from .remote_services import RemoteServiceError


class EnableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


def create_remote_router(manager, runtime, require_admin, check_csrf):
    router = APIRouter(prefix="/remote-services", tags=["remote-services"])

    def current_admin(actor):
        with runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
        require(can_manage_platform(current), "forbidden", "管理服务连接需要平台管理权限。", 403)

    async def reconcile_coordination(request):
        coordinator = getattr(request.app.state, "coordination", None)
        if coordinator is not None:
            await coordinator.reconcile()

    @router.get("")
    def connections(actor=Depends(require_admin)):
        return manager.list_services()

    @router.post("", dependencies=[Depends(check_csrf)])
    async def save(request: Request, actor=Depends(require_admin)):
        raw = await request.body()
        require(len(raw) <= 32768, "remote_config_size", "服务配置内容过大。", 422)
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            from .errors import PlatformError

            raise PlatformError("remote_config_format", "服务配置需要有效 JSON。", 422) from None
        async with runtime.lock:
            current_admin(actor)
            require(
                isinstance(payload, dict), "remote_config_format", "服务配置需要 JSON 对象。", 422
            )
            result = manager.save_connection(payload, require_revision=True)
            runtime.registry = runtime.build_remote_registry(manager.enabled_bindings())
        await reconcile_coordination(request)
        return result

    @router.get("/monitoring/summary")
    def monitoring_summary(actor=Depends(require_admin)):
        monitor = manager._monitor
        if hasattr(monitor, "summary"):
            return monitor.summary()
        return {"mode": "independent", "state": "unconfigured", "summary": None, "items": [],
                "message": "尚未配置独立监控服务。", "last_received_at": None, "interval_seconds": 5}

    @router.post("/{service_id}/discover", dependencies=[Depends(check_csrf)])
    async def discover(service_id: str, request: Request, actor=Depends(require_admin)):
        # Network discovery must not hold the invocation lock. The manager pins
        # and rechecks the connection revision while it waits for the response.
        result = await manager.discover(service_id, before_commit=lambda: current_admin(actor))
        async with runtime.lock:
            runtime.registry = runtime.build_remote_registry(manager.enabled_bindings())
        await reconcile_coordination(request)
        return result

    @router.post("/{service_id}/health", dependencies=[Depends(check_csrf)])
    async def health(service_id: str, actor=Depends(require_admin)):
        result = await manager.health(service_id)
        current_admin(actor)
        return result

    @router.post("/{service_id}/admission", dependencies=[Depends(check_csrf)])
    async def admission(service_id: str, body: EnableInput, request: Request, actor=Depends(require_admin)):
        # The callback executes synchronously after network work; it only reads
        # trusted local definitions. Publication performs the same checks again.
        result = await manager.admit(
            service_id,
            body.expected_revision,
            host_check=lambda document: runtime.check_remote_registration(service_id, document),
            before_commit=lambda: current_admin(actor),
        )
        async with runtime.lock:
            runtime.registry = runtime.build_remote_registry(manager.enabled_bindings())
        await reconcile_coordination(request)
        return result

    @router.post("/{service_id}/enable", dependencies=[Depends(check_csrf)])
    async def enable(service_id: str, body: EnableInput, request: Request, actor=Depends(require_admin)):
        async with runtime.lock:
            current_admin(actor)
            result=runtime.set_remote_publication(service_id, True, body.expected_revision)
        await reconcile_coordination(request)
        return result

    @router.post("/{service_id}/disable", dependencies=[Depends(check_csrf)])
    async def disable(service_id: str, body: EnableInput, request: Request, actor=Depends(require_admin)):
        async with runtime.lock:
            current_admin(actor)
            result=runtime.set_remote_publication(service_id, False, body.expected_revision)
        await reconcile_coordination(request)
        return result

    return router


def remote_error_response(exc: RemoteServiceError):
    code = exc.code
    if code == "remote_monitor_unavailable":
        status = 503
    elif code in ("remote_config_corrupt", "remote_config_write_failed", "remote_secret_unavailable"):
        status = 500
    elif code == "remote_revision_required":
        status = 422
    elif code in ("remote_admission_required", "remote_admission_busy"):
        status = 409
    else:
        status = (
            404
            if code.endswith("not_found")
            else (
                504
                if "timeout" in code
                else (
                    409
                    if any(s in code for s in ("revision", "changed", "disabled", "not_discovered"))
                    else (
                        422
                        if any(s in code for s in ("invalid", "manifest", "config", "schema"))
                        else 502
                    )
                )
            )
        )
    return JSONResponse({"error": {"code": code, "message": exc.message}}, status_code=status)
