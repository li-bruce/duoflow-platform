"""Run with uvicorn monitoring_service.main:create_app --factory, separately."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .protocol import REPORT_BYTES, Report, Targets
from .store import MonitorError, MonitorStore


async def body(request, limit):
    result = bytearray()
    try:
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(result) + len(chunk) > limit:
                    raise MonitorError("monitor_body_size", 413)
                result.extend(chunk)
        return json.loads(result, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise MonitorError("monitor_body_format") from None
    except TimeoutError:
        raise MonitorError("monitor_body_timeout", 408) from None


def bearer(request):
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def create_app(data_dir=None, *, transport=None, clock=None, start_worker=True):
    configured = data_dir or os.getenv("DUOFLOW_MONITOR_DATA_DIR")
    if not configured:
        raise ValueError("Configure an independent DUOFLOW_MONITOR_DATA_DIR")
    store = MonitorStore(Path(configured).resolve(), transport=transport, clock=clock)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(store.monitor.run(stop)) if start_worker else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task

    app = FastAPI(title="DuoFlow 独立监控服务", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.monitor = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.exception_handler(MonitorError)
    async def error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": "监控请求未被接受，请检查配置、权限或修订。"}},
                            status_code=exc.status)

    @app.exception_handler(ValidationError)
    async def invalid(request, exc):
        # Pydantic errors include submitted input. Never return those details.
        return JSONResponse({"error": {"code": "monitor_contract", "message": "监控字段或参数无效。"}}, status_code=422)

    @app.get("/health")
    def health():
        return {"service": "duoflow-monitor", "status": "ok", "collector_id": store.bootstrap["collector_id"]}

    @app.put("/api/v1/monitor/targets")
    async def targets(request: Request):
        store.authorize(bearer(request))
        command = Targets.model_validate(await body(request, 2 * 1024 * 1024))
        return store.replace_targets(command)

    @app.get("/api/v1/monitor/snapshot")
    def snapshot(request: Request):
        store.authorize(bearer(request))
        return store.snapshot()

    @app.post("/api/v1/monitor/targets/{service_id}/probe")
    async def probe(service_id: str, request: Request):
        store.authorize(bearer(request))
        return await store.monitor.probe(service_id)

    @app.get("/api/v1/monitor/targets/{service_id}/reporting")
    def credentials(service_id: str, request: Request):
        store.authorize(bearer(request))
        return JSONResponse(store.reporting_credentials(service_id), headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/monitor/reports/{service_id}")
    async def report(service_id: str, request: Request):
        token = bearer(request)
        store.authorize_report(service_id, token)
        value = Report.model_validate(await body(request, REPORT_BYTES))
        return store.report(service_id, token, value)

    return app
