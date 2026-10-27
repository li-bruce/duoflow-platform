"""Short-lived service-to-service permission decisions for trusted remote modules.

The ticket is a bearer secret sent only to the administered capability service.
It never authenticates browser-supplied resource facts. A remote service must first
validate its existing platform Bearer credential and load facts from its repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import APIRouter, Request
from jsonschema import Draft202012Validator

from .errors import PlatformError, require
from .identity import authorization_snapshot, refresh_actor


MAX_REQUEST_BYTES = 16 * 1024
MAX_TICKET_BYTES = 12 * 1024
IDENTIFIER = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"}
FACT_VALUE = {
    "oneOf": [
        {"type": "string", "minLength": 1, "maxLength": 160},
        {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 160},
        },
    ]
}
DECISION_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["request_id", "operation", "permission", "facts"],
    "properties": {
        "request_id": IDENTIFIER,
        "operation": IDENTIFIER,
        "permission": IDENTIFIER,
        "facts": {
            "type": "object",
            "maxProperties": 32,
            "propertyNames": {"pattern": r"^[a-z][a-z0-9_]{0,79}$"},
            "additionalProperties": FACT_VALUE,
        },
    },
}


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class AccessBridge:
    def __init__(self, runtime, *, secret=None, ttl_seconds=45, clock=None):
        require(
            type(ttl_seconds) is int and 1 <= ttl_seconds <= 60,
            "access_bridge_config",
            "授权票据有效期应为 1 至 60 秒",
            500,
        )
        require(
            secret is None or (isinstance(secret, bytes) and len(secret) >= 32),
            "access_bridge_config",
            "授权票据签名密钥配置无效",
            500,
        )
        self.runtime = runtime
        self.secret = secret or secrets.token_bytes(32)
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time

    @staticmethod
    def _binding(spec):
        return {
            key: spec.get(key)
            for key in (
                "id",
                "version",
                "module_id",
                "module_revision",
                "service_id",
                "binding_revision",
            )
        }

    def issue(self, actor, spec, request_id, operation, *, source_chain=()):
        require(
            isinstance(source_chain, (list, tuple)) and len(source_chain) <= 4
            and all(isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{2,119}", item)
                    for item in source_chain)
            and len(set(source_chain)) == len(source_chain),
            "source_read_cycle", "来源读取层级无效。", 409,
        )
        require(
            isinstance(request_id, str)
            and re.fullmatch(IDENTIFIER["pattern"], request_id) is not None,
            "access_bridge_request",
            "授权请求标识无效",
            422,
        )
        require(
            isinstance(operation, str)
            and re.fullmatch(IDENTIFIER["pattern"], operation) is not None,
            "access_bridge_operation",
            "授权操作标识无效",
            422,
        )
        require(
            bool(spec.get("service_id"))
            and self.runtime.access.module_registered(spec["module_id"]),
            "access_bridge_module",
            "该服务尚未接入独立模块权限",
            403,
        )
        with self.runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
        actual = self.runtime.registry.get(spec["id"], current)
        require(
            self._binding(actual) == self._binding(spec),
            "access_bridge_binding",
            "服务连接已更新",
            409,
        )
        require(
            self.runtime.availability(actual)["available"],
            "access_bridge_unavailable",
            "服务当前不可用",
            503,
        )
        started = self.clock()
        claims = {
            "v": 1,
            "actor": authorization_snapshot(current),
            "stamp": self.runtime.access.authorization_stamp(current),
            "binding": self._binding(actual),
            "request_id": request_id,
            "operation": operation,
            "iat": started,
            "exp": started + self.ttl_seconds,
            "nonce": secrets.token_hex(12),
            "source_chain": list(source_chain),
        }
        encoded = _b64(
            json.dumps(claims, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )
        ticket = (
            encoded
            + "."
            + _b64(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        )
        require(
            len(ticket) <= MAX_TICKET_BYTES,
            "access_bridge_ticket_size",
            "授权身份上下文超过传输上限",
            413,
        )
        return ticket

    def _verify(self, ticket):
        require(
            isinstance(ticket, str) and 0 < len(ticket) <= MAX_TICKET_BYTES,
            "access_ticket_invalid",
            "授权票据无效",
            401,
        )
        try:
            encoded, signature = ticket.split(".")
            require(
                hmac.compare_digest(
                    _unb64(signature),
                    hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest(),
                ),
                "access_ticket_invalid",
                "授权票据无效",
                401,
            )
            claims = json.loads(_unb64(encoded))
            issued, expires = claims["iat"], claims["exp"]
            require(
                type(issued) in (int, float)
                and type(expires) in (int, float)
                and 0 < expires - issued <= 60
                and issued <= self.clock() + 2
                and self.clock() < expires,
                "access_ticket_expired",
                "授权票据已过期",
                401,
            )
            require(
                claims["v"] == 1
                and isinstance(claims["actor"], dict)
                and isinstance(claims["binding"], dict),
                "access_ticket_invalid",
                "授权票据无效",
                401,
            )
            return claims
        except PlatformError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise PlatformError("access_ticket_invalid", "授权票据无效", 401) from None

    def decide(self, ticket, request_id, permission, facts, *, operation=None):
        """Read-only; fail closed. Never return grant IDs or the ticket's claims."""
        try:
            claims = self._verify(ticket)
            require(
                request_id == claims["request_id"],
                "access_ticket_request_mismatch",
                "授权请求不匹配",
                403,
            )
            require(
                operation is None or operation == claims["operation"],
                "access_ticket_operation_mismatch",
                "授权操作不匹配",
                403,
            )
            request = {
                "request_id": request_id,
                "permission": permission,
                "facts": facts,
                "operation": operation or claims["operation"],
            }
            require(
                Draft202012Validator(DECISION_REQUEST_SCHEMA).is_valid(request),
                "access_decision_contract",
                "授权判断输入无效",
                422,
            )
            with self.runtime.sessions() as db:
                current = refresh_actor(db, claims["actor"], require_unchanged=True)
            require(
                self.runtime.access.authorization_stamp(current) == claims["stamp"],
                "access_authorization_changed",
                "授权已变化",
                403,
            )
            spec = self.runtime.registry.get(claims["binding"]["id"], current)
            require(
                self._binding(spec) == claims["binding"],
                "access_bridge_binding",
                "服务连接已更新",
                403,
            )
            require(
                self.runtime.availability(spec)["available"],
                "access_bridge_unavailable",
                "服务当前不可用",
                503,
            )
            require(
                permission.startswith(spec["module_id"] + ".")
                and permission in self.runtime.access.capability_permissions(spec["id"]),
                "access_ticket_permission_mismatch",
                "权限不属于当前能力",
                403,
            )
            decision = self.runtime.access.check(current, permission, facts)
            return {key: decision[key] for key in ("allowed", "reason", "policy_revision")}
        except PlatformError as error:
            return {"allowed": False, "reason": error.code, "policy_revision": 0}


def create_access_bridge_router(bridge):
    router = APIRouter()

    @router.post("/api/v1/access/remote-decisions")
    async def remote_decision(request: Request):
        # Bound the stream before reading/parsing it. No browser cookie or CSRF
        # credential can replace a service ticket on this route.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            require(len(body) <= MAX_REQUEST_BYTES, "access_decision_size", "授权判断请求过大", 413)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise PlatformError("access_decision_contract", "授权判断输入无效", 422) from None
        require(
            Draft202012Validator(DECISION_REQUEST_SCHEMA).is_valid(payload),
            "access_decision_contract",
            "授权判断输入无效",
            422,
        )
        return bridge.decide(
            request.headers.get("X-Platform-Access", ""),
            payload["request_id"],
            payload["permission"],
            payload["facts"],
            operation=payload["operation"],
        )

    return router
