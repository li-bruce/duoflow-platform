import json

from fastapi import APIRouter, Depends, Request

from .devtools import MAX_DECLARATION_BYTES
from .errors import PlatformError, require


def create_devtools_router(service, require_admin, check_csrf):
    router = APIRouter(prefix="/api/v1/devtools", tags=["devtools"])

    @router.get("/catalog")
    def catalog(actor=Depends(require_admin)):
        return service.catalog(actor)

    @router.post("/validate", dependencies=[Depends(check_csrf)])
    async def validate(request: Request, actor=Depends(require_admin)):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            require(len(body) <= MAX_DECLARATION_BYTES, "devtools_size", "声明超过 256 KiB。", 413)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise PlatformError("devtools_json", "请提供有效 JSON 声明。", 422) from None
        require(
            isinstance(value, dict) and set(value) == {"kind", "definition"},
            "devtools_input",
            "请求需要 kind 和 definition，且不接受其他字段。",
            422,
        )
        return service.validate(actor, value["kind"], value["definition"])

    @router.get("/instances")
    def instances(actor=Depends(require_admin)):
        return service.instances(actor)

    @router.get("/instances/{instance_id}")
    async def instance(instance_id: str, actor=Depends(require_admin)):
        return await service.observe(actor, instance_id)

    return router
