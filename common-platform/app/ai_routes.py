"""Secret input controls and short-lived module AI callbacks."""

import json

from fastapi import APIRouter, Depends, Request

from .errors import PlatformError, require


async def read_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        require(len(body) <= limit, "ai_request_size", "AI 请求超过允许大小。", 413)
    try:
        def invalid_constant(value):
            raise ValueError()

        return json.loads(body, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise PlatformError("ai_contract", "AI 请求需要有效 JSON。", 422) from None


def create_ai_router(service, require_admin, check_csrf):
    router = APIRouter(tags=["module-ai"])

    @router.get("/api/v1/ai-services")
    def list_services(actor=Depends(require_admin)):
        return service.list_services(actor)

    @router.put("/api/v1/ai-services/{module_id}/{service_id}/binding",
                dependencies=[Depends(check_csrf)])
    async def save_binding(module_id: str, service_id: str, request: Request,
                           actor=Depends(require_admin)):
        return await service.save_binding(actor, module_id, service_id, await read_body(request, 4096))

    @router.get("/api/v1/ai-services/{module_id}/{service_id}/module-config")
    async def module_config(module_id: str, service_id: str, actor=Depends(require_admin)):
        return await service.module_config(actor, module_id, service_id)

    @router.put("/api/v1/ai-services/{module_id}/{service_id}/module-config",
                dependencies=[Depends(check_csrf)])
    async def save_module_config(module_id: str, service_id: str, request: Request,
                                 actor=Depends(require_admin)):
        return await service.module_config(actor, module_id, service_id,
                                           await read_body(request, 32768))

    @router.post("/api/v1/ai-services/{module_id}/{service_id}/test",
                 dependencies=[Depends(check_csrf)])
    async def test(module_id: str, service_id: str, request: Request,
                   actor=Depends(require_admin)):
        return await service.test(actor, module_id, service_id, await read_body(request, 4096))

    @router.post("/api/v1/ai/resolve")
    async def resolve(request: Request):
        return await service.resolve(request.headers.get("X-Platform-Access", ""),
                                     await read_body(request, 16384))

    @router.post("/api/v1/ai/validate")
    async def validate(request: Request):
        return await service.validate(request.headers.get("X-Platform-Access", ""),
                                      await read_body(request, 16384))

    @router.post("/api/v1/ai/chat")
    async def chat(request: Request):
        return await service.chat(request.headers.get("X-Platform-Access", ""),
                                  await read_body(request, 65536))

    return router
