"""Portable server-only source read callback; no platform imports or credentials returned."""

import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx


class SourceReadError(RuntimeError):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code = code
        self.status = status if type(status) is int and 400 <= status <= 599 else 502


class SourceReadClient:
    def __init__(self, callback_url, *, timeout_seconds=8, transport=None):
        parsed = urlsplit(callback_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != "/api/v1/access/source-reads"
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30):
            raise SourceReadError("source_read_configuration", 500)
        self.callback_url, self.timeout, self.transport = callback_url, timeout_seconds, transport

    async def read(self, *, ticket, request_id, operation, permission, facts, source, read_input=None):
        if not isinstance(ticket, str) or not 0 < len(ticket) <= 12288:
            raise SourceReadError("access_ticket_invalid", 401)
        payload = dict(source_read_version="1.0", request_id=request_id, operation=operation,
                       permission=permission, facts=facts, source=source)
        if read_input is not None:
            payload["read_input"] = read_input
        body = json.dumps(payload, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if len(body) > 16384:
            raise SourceReadError("source_read_size", 413)
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                             follow_redirects=False, timeout=self.timeout) as client:
                    async with client.stream("POST", self.callback_url, content=body,
                            headers={"Content-Type": "application/json", "Accept-Encoding": "identity",
                                     "X-Platform-Access": ticket}) as response:
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise SourceReadError("source_read_response", 502)
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > (1100000 if response.is_success else 4096):
                                raise SourceReadError("source_read_response_size", 502)
                            content.extend(chunk)
                        raw = content.decode("utf-8")
                        if ticket in raw:
                            raise SourceReadError("source_read_credential", 502)
                        result = json.loads(raw)
                        if not response.is_success:
                            code = result.get("error", {}).get("code") if isinstance(result, dict) else None
                            raise SourceReadError(code if isinstance(code, str) and
                                re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "source_read_denied", response.status_code)
            projection = result.get("projection", {})
            if (set(result) != {"source_read_version", "source", "projection"}
                    or result["source_read_version"] != "1.0" or result["source"] != source
                    or projection.get("object_ref") != source["object_ref"]
                    or projection.get("revision") != source["revision"]
                    or not isinstance(projection.get("state"), str)
                    or not isinstance(projection.get("data"), dict)
                    or not isinstance(projection.get("allowed_actions"), list)):
                raise SourceReadError("source_read_response", 502)
            return result
        except SourceReadError:
            raise
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, KeyError, AttributeError):
            raise SourceReadError("source_read_unavailable") from None
