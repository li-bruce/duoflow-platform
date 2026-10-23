"""Portable module-kit v1 helpers for native Python tools (standard library only).

This file is vendored as backend/_module_kit.py. It is not an authorization
engine: the injected platform access service makes every permission decision.
"""

from __future__ import annotations

import json


SDK_VERSION = "1.0.0"
MAX_INPUT_BYTES = 100_000
MAX_RESULT_BYTES = 256_000


class KitError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def _json_size(value, limit: int, *, output: bool = False):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise KitError(
            "tool_result_invalid" if output else "tool_input_invalid",
            "工具结果不是有效 JSON。" if output else "输入不是有效 JSON。",
            500 if output else 422,
        ) from None
    if len(encoded) > limit:
        raise KitError(
            "tool_result_too_large" if output else "tool_input_too_large",
            "工具结果超过大小限制。" if output else "输入超过大小限制。",
            500 if output else 413,
        )


def require_organization_access(ctx, permission: str) -> dict:
    """Authorize the caller's organization, never a scope supplied in payload.

    Only appropriate for an actor-scoped tool with no stored business object.
    Object tools must instead derive facts from their own trusted repository.
    """
    actor = getattr(ctx, "actor", None)
    if (
        not isinstance(actor, dict)
        or not isinstance(actor.get("id"), str)
        or not actor["id"].strip()
        or actor.get("enabled") is False
    ):
        raise KitError("tool_identity_required", "需要有效的平台身份。", 403)
    organization_id = actor.get("organization_id")
    if (
        not isinstance(organization_id, str)
        or not organization_id.strip()
        or organization_id == "*"
    ):
        raise KitError("tool_organization_required", "请先由平台管理员分配所属机构。", 403)
    access = getattr(ctx, "access", None)
    if not callable(getattr(access, "check", None)):
        raise KitError("tool_access_unavailable", "平台权限服务不可用。", 503)
    facts = {"organization_id": organization_id}
    try:
        decision = access.check(actor, permission, facts)
    except Exception:
        # Do not expose provider errors or attempt a legacy-role fallback.
        raise KitError("tool_access_unavailable", "平台权限检查失败，请稍后重试。", 503) from None
    if not isinstance(decision, dict) or decision.get("allowed") is not True:
        raise KitError("tool_forbidden", "未获得本机构的工具使用授权。", 403)
    return facts


def run_tool(ctx, *, permission: str, payload: dict, handler) -> dict:
    """Check current scope and call a pure function returning summary + data.

    Platform input/output Schema validation, idempotency, CSRF, lifecycle and
    invocation history remain platform responsibilities. This wrapper neither
    persists records nor commits the injected transaction.
    """
    require_organization_access(ctx, permission)
    if not isinstance(payload, dict):
        raise KitError("tool_input_invalid", "输入必须是 JSON 对象。", 422)
    _json_size(payload, MAX_INPUT_BYTES)
    try:
        result = handler(payload)
    except ValueError:
        raise KitError(
            "tool_input_invalid", "输入不符合工具要求，请检查字段与内容。", 422
        ) from None
    if (
        not isinstance(result, dict)
        or set(result) != {"summary", "data"}
        or not isinstance(result["summary"], str)
        or not 1 <= len(result["summary"]) <= 4000
        or not isinstance(result["data"], dict)
    ):
        raise KitError("tool_result_invalid", "工具结果不符合 summary/data 契约。", 500)
    _json_size(result, MAX_RESULT_BYTES, output=True)
    return result
