"""Model management endpoints; no provider credentials enter response schemas."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .model_gateway import ModelGateway, ModelGatewayError


def _error(error: ModelGatewayError) -> JSONResponse:
    if error.code == "model_profile_not_found":
        status = 404
    elif error.code == "model_timeout":
        status = 504
    elif error.code.startswith("invalid_"):
        status = 422
    elif error.code in {"model_disabled", "model_not_configured", "model_destination_changed"}:
        status = 409
    elif error.code.startswith("model_config") or error.code == "model_secret_unavailable":
        status = 503
    else:
        status = 502
    return JSONResponse(
        status_code=status, content={"error": {"code": error.code, "message": error.message}}
    )


def create_model_router(
    gateway: ModelGateway, require_admin: Callable, require_user: Callable, check_csrf: Callable
) -> APIRouter:
    router = APIRouter(prefix="/models", tags=["models"])

    @router.get("")
    async def list_models(actor=Depends(require_user)):
        role = actor.get("role") if isinstance(actor, dict) else getattr(actor, "role", None)
        try:
            return gateway.list_profiles(admin=role == "admin")
        except ModelGatewayError as error:
            return _error(error)

    @router.post("", dependencies=[Depends(check_csrf)])
    async def save_model(request: Request, actor=Depends(require_admin)):
        # Avoid validation errors echoing a submitted api_key in Pydantic's input.
        try:
            raw = await request.body()
            if len(raw) > 32768:
                raise ModelGatewayError("invalid_model_config", "模型配置内容过大。")
            try:
                payload = await request.json()
            except (ValueError, UnicodeError):
                raise ModelGatewayError("invalid_model_config", "模型配置应为有效 JSON。") from None
            return gateway.save_profile(payload)
        except ModelGatewayError as error:
            return _error(error)

    @router.delete("/{profile_id}", dependencies=[Depends(check_csrf)])
    async def delete_model(profile_id: str, actor=Depends(require_admin)):
        try:
            gateway.delete_profile(profile_id)
            return {"ok": True, "default_profile_id": gateway.list_profiles()["default_profile_id"]}
        except ModelGatewayError as error:
            return _error(error)

    @router.post("/{profile_id}/test", dependencies=[Depends(check_csrf)])
    async def test_model(profile_id: str, actor=Depends(require_admin)):
        try:
            return await gateway.test_connection(profile_id)
        except ModelGatewayError as error:
            return _error(error)

    return router
