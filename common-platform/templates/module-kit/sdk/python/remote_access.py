"""Portable access v1 client for trusted Python remote services (Python 3.11+).

Validate the incoming service Bearer first, read facts from your repository,
then call ``decide`` with the original request ID, operation and access ticket.
This client neither authenticates browser input nor replaces a state machine,
business revision checks or idempotency. Tickets must not be saved or logged.
Only httpx and the standard library are required; no platform imports occur.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import math
import re
from urllib.parse import urlsplit

import httpx


_ENDPOINT = "/api/v1/access/remote-decisions"
_MAX_REQUEST = 16 * 1024
_MAX_TICKET = 12 * 1024
_MAX_RESPONSE = 4096
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
_FACT_KEY = re.compile(r"[a-z][a-z0-9_]{0,79}")
# Return public reason codes only, never arbitrary text supplied by a server.
# New central deny reasons remain denied as access_decision_invalid until this
# portable client's allowlist is updated; they cannot expand authorization.
_DENIAL_REASONS = frozenset(
    {
        "permission_unknown",
        "identity_inactive",
        "module_disabled",
        "policy_revision_changed",
        "required_fact_missing",
        "separation_of_duties",
        "actor_relation_mismatch",
        "fact_condition_failed",
        "grant_scope_mismatch",
        "command_unmapped",
        "access_ticket_invalid",
        "access_ticket_expired",
        "access_ticket_request_mismatch",
        "access_ticket_operation_mismatch",
        "access_decision_contract",
        "access_authorization_changed",
        "access_bridge_binding",
        "access_bridge_unavailable",
        "access_ticket_permission_mismatch",
        "actor_disabled",
        "authorization_changed",
        "capability_missing",
        "forbidden",
    }
)


def _deny(reason):
    return {"allowed": False, "reason": reason, "policy_revision": 0}


def service_bearer_matches(authorization: str | None, expected_token: str) -> bool:
    """Compare the configured service secret in constant time; never default open."""
    if not isinstance(authorization, str) or not isinstance(expected_token, str):
        return False
    if not expected_token or any(ord(c) < 33 or ord(c) > 126 for c in expected_token):
        return False
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token:
        return False
    if any(ord(c) < 33 or ord(c) > 126 for c in token):
        return False
    return hmac.compare_digest(token.encode("ascii"), expected_token.encode("ascii"))


def _valid_request(request_id, operation, permission, facts):
    if not all(
        isinstance(v, str) and _IDENTIFIER.fullmatch(v) for v in (request_id, operation, permission)
    ):
        return False
    if not isinstance(facts, dict) or len(facts) > 32:
        return False
    for key, value in facts.items():
        if not isinstance(key, str) or not _FACT_KEY.fullmatch(key):
            return False
        if isinstance(value, str):
            if not 1 <= len(value) <= 160:
                return False
        elif isinstance(value, list):
            if not 1 <= len(value) <= 20 or not all(
                isinstance(v, str) and 1 <= len(v) <= 160 for v in value
            ):
                return False
            if len(set(value)) != len(value):
                return False
        else:
            return False
    return True


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Non-finite JSON number")


class RemoteAccessClient:
    """Fixed deployment callback, bounded I/O and fail-closed central decisions.

    ``transport`` is the standard httpx test/transport seam. Each call closes its
    own HTTP client and never stores request facts or tickets on this instance.
    Caller cancellation propagates; operational failures return a safe denial.
    """

    def __init__(self, callback_url: str, *, timeout_seconds: float = 4.0, transport=None):
        try:
            if not isinstance(callback_url, str) or any(
                ord(c) < 33 or ord(c) == 127 for c in callback_url
            ):
                raise ValueError
            endpoint = urlsplit(callback_url)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.path != _ENDPOINT
                or "?" in callback_url
                or "#" in callback_url
                or (endpoint.port is not None and not 1 <= endpoint.port <= 65535)
            ):
                raise ValueError
            # Validate the actual HTTP library's URL parser as well.
            parsed = httpx.URL(callback_url)
            if parsed.raw_path != _ENDPOINT.encode("ascii") or not parsed.host:
                raise ValueError
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("Invalid configured access-decision endpoint") from None
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 10
        ):
            raise ValueError(
                "Access decision timeout must be greater than zero and at most 10 seconds"
            )
        self._callback_url = parsed
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport

    async def decide(self, *, ticket, request_id, operation, permission, facts) -> dict:
        if (
            not isinstance(ticket, str)
            or not 0 < len(ticket) <= _MAX_TICKET
            or any(ord(c) < 33 or ord(c) > 126 for c in ticket)
        ):
            return _deny("access_ticket_invalid")
        if not _valid_request(request_id, operation, permission, facts):
            return _deny("access_decision_contract")
        try:
            body = json.dumps(
                {
                    "request_id": request_id,
                    "operation": operation,
                    "permission": permission,
                    "facts": facts,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return _deny("access_decision_contract")
        if len(body) > _MAX_REQUEST:
            return _deny("access_decision_size")
        try:
            # A total deadline also bounds custom transports and slow trickles.
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as client:
                    async with client.stream(
                        "POST",
                        self._callback_url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "Accept-Encoding": "identity",
                            "X-Platform-Access": ticket,
                        },
                    ) as response:
                        if response.status_code != 200:
                            return _deny("access_decision_unavailable")
                        # Reject compressed bodies to avoid decompression expansion.
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            return _deny("access_decision_invalid")
                        length = response.headers.get("content-length")
                        if length is not None and (
                            not length.isdigit() or int(length) > _MAX_RESPONSE
                        ):
                            return _deny("access_decision_response_size")
                        received = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(received) + len(chunk) > _MAX_RESPONSE:
                                return _deny("access_decision_response_size")
                            received.extend(chunk)
            result = json.loads(
                received.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        except (ValueError, UnicodeError, RecursionError):
            return _deny("access_decision_invalid")
        except (httpx.HTTPError, OSError, TimeoutError):
            return _deny("access_decision_unavailable")
        if (
            not isinstance(result, dict)
            or set(result) != {"allowed", "reason", "policy_revision"}
            or type(result["allowed"]) is not bool
            or not isinstance(result["reason"], str)
            or type(result["policy_revision"]) is not int
            or result["policy_revision"] < 0
        ):
            return _deny("access_decision_invalid")
        if result["allowed"]:
            if result["reason"] != "allowed" or result["policy_revision"] < 1:
                return _deny("access_decision_invalid")
        elif result["reason"] not in _DENIAL_REASONS:
            return _deny("access_decision_invalid")
        return {key: result[key] for key in ("allowed", "reason", "policy_revision")}
