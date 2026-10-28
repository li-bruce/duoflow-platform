"""Portable server-only AI service manager v1 (httpx + cryptography).

One process owns data_dir. AES-GCM and a local 0600 key protect a copied config,
not access to the entire directory. Set a private Windows directory ACL as well.
HTTP adapters MUST verify their private service Bearer before calling control().
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AIServiceError(RuntimeError):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code, self.status = code, status


def _need(ok, code="ai_configuration", status=422):
    if not ok:
        raise AIServiceError(code, status)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{2,119}", value)


def _scope_identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value)


def _facts(value):
    def fact_string(entry):
        return isinstance(entry, str) and 0 < len(entry) <= 160
    return isinstance(value, dict) and len(value) <= 32 and all(
        isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key)
        and (fact_string(item) or isinstance(item, list) and 0 < len(item) <= 20
             and all(map(fact_string, item)) and len(set(item)) == len(item)) for key, item in value.items())


_PUBLIC = ("id", "name", "base_url", "model", "enabled", "temperature", "max_tokens", "timeout_seconds")
_PROFILE_FIELDS = (set(_PUBLIC) - {"id"}) | {"api_key", "clear_api_key"}
_FINISH = {"stop", "length", "max_tokens", "tool_calls", "content_filter", "function_call"}


def _redact(value, secrets):
    if isinstance(value, str):
        for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
            value = value.replace(secret, "【密钥已隐藏】")
        return value
    if isinstance(value, list):
        return [_redact(child, secrets) for child in value]
    if isinstance(value, dict):
        return {_redact(key, secrets): _redact(child, secrets) for key, child in value.items()}
    return value


def _base_url(value, *, origin_only=False):
    _need(isinstance(value, str) and 0 < len(value) <= 2048
          and not re.search(r"[\s\\%?#]", value))
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        _need(parsed.scheme in {"http", "https"} and host and parsed.username is None
              and parsed.password is None and (port is None or 0 < port <= 65535)
              and (parsed.scheme == "https" or loopback)
              and (not origin_only or parsed.path in {"", "/"}))
        return urlunsplit((parsed.scheme, parsed.netloc, "" if origin_only else parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError):
        raise AIServiceError("ai_configuration", 422) from None


def _text(value, maximum):
    _need(isinstance(value, str) and value.strip() and len(value) <= maximum
          and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    return value.strip()


def _number(value, minimum, maximum, *, integer=False):
    _need(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
          and (not integer or type(value) is int))
    return value


def _messages(messages):
    _need(isinstance(messages, list) and 0 < len(messages) <= 64 and all(
        isinstance(entry, dict) and set(entry) == {"role", "content"}
        and entry["role"] in {"system", "developer", "user", "assistant"}
        and isinstance(entry["content"], str) and len(entry["content"]) <= 16000 for entry in messages), "ai_request", 422)
    _need(len(json.dumps(messages, ensure_ascii=False).encode()) <= 60000, "ai_request_size", 413)


def _result(value):
    _need(isinstance(value, dict) and {"content", "model", "tool_calls"} <= set(value)
          and not set(value) - {"content", "model", "tool_calls", "finish_reason", "usage"}
          and isinstance(value["content"], str) and isinstance(value["model"], str)
          and value["tool_calls"] == []
          and ("finish_reason" not in value or value["finish_reason"] in _FINISH)
          and ("usage" not in value or isinstance(value["usage"], dict) and all(
              key in {"prompt_tokens", "completion_tokens", "total_tokens"}
              and type(count) is int and count >= 0 for key, count in value["usage"].items())),
          "ai_response", 502)
    return value


class AIServiceManager:
    def __init__(self, *, services, data_dir, platform_base_url=None, transport=None):
        _need(isinstance(services, list) and 0 < len(services) <= 16 and all(
            isinstance(service, dict) and set(service) == {"id", "title", "description", "capability_ids", "supported_sources"}
            and _identifier(service["id"]) and isinstance(service["title"], str) and 0 < len(service["title"]) <= 200
            and isinstance(service["description"], str) and 0 < len(service["description"]) <= 2000 and isinstance(service["capability_ids"], list)
            and 0 < len(service["capability_ids"]) <= 64 and all(map(_identifier, service["capability_ids"]))
            and len(set(service["capability_ids"])) == len(service["capability_ids"])
            and isinstance(service["supported_sources"], list) and service["supported_sources"]
            and all(source in {"platform", "module"} for source in service["supported_sources"])
            and len(set(service["supported_sources"])) == len(service["supported_sources"])
            for service in services) and len({service["id"] for service in services}) == len(services),
            "ai_configuration", 500)
        self._services = {service["id"]: deepcopy(service) for service in services}
        self._directory = Path(data_dir).absolute()
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._filename = self._directory / "module-ai.config.enc"
        self._keyfile = self._directory / "module-ai.key"
        self._platform = None if platform_base_url is None else _base_url(platform_base_url, origin_only=True)
        self._transport = transport
        self._lock = threading.RLock()
        self._read()

    @property
    def services(self):
        return deepcopy(list(self._services.values()))

    @property
    def platform_connected(self):
        return self._platform is not None

    def _service(self, service_id):
        _need(service_id in self._services, "ai_service_unknown", 404)
        return self._services[service_id]

    def _key(self, *, create=False):
        try:
            if create and not self._keyfile.exists():
                descriptor = os.open(self._keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(os.urandom(32))
                    output.flush()
                    os.fsync(output.fileno())
            key = self._keyfile.read_bytes()
            _need(len(key) == 32, "ai_secret_unavailable", 503)
            return key
        except OSError:
            raise AIServiceError("ai_secret_unavailable") from None

    def _read(self):
        if not self._filename.exists():
            return {"version": 1, "services": {}}
        try:
            content = self._filename.read_bytes()
            _need(29 <= len(content) <= 1048576, "ai_config_corrupt", 503)
            # Same wire layout as Node: nonce(12), tag(16), ciphertext.
            raw = AESGCM(self._key()).decrypt(content[:12], content[28:] + content[12:28], None)
            state = json.loads(raw)
            _need(state["version"] == 1 and isinstance(state["services"], dict) and all(
                isinstance(entry, dict) and type(entry["revision"]) is int and entry["revision"] >= 1
                and isinstance(entry["profile"], dict) for entry in state["services"].values()),
                "ai_config_corrupt", 503)
            return state
        except Exception:
            raise AIServiceError("ai_config_corrupt") from None

    def _write(self, state):
        temporary = None
        try:
            nonce = os.urandom(12)
            encrypted = AESGCM(self._key(create=True)).encrypt(nonce, json.dumps(state, ensure_ascii=False, allow_nan=False).encode(), None)
            descriptor, temporary = tempfile.mkstemp(prefix=".module-ai-", dir=self._directory)
            with os.fdopen(descriptor, "wb") as output:
                output.write(nonce + encrypted[-16:] + encrypted[:-16])
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._filename)
        except (OSError, ValueError, TypeError):
            raise AIServiceError("ai_config_write_failed") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _secrets(self, state=None):
        with self._lock:
            return [entry["profile"].get("api_key", "") for entry in (state or self._read())["services"].values()]

    def get_config(self, service_id):
        self._service(service_id)
        with self._lock:
            entry = self._read()["services"].get(service_id)
            if entry is None:
                return {"revision": 0, "profile": None}
            return {"revision": entry["revision"], "profile": {
                **{key: entry["profile"][key] for key in _PUBLIC},
                "api_key_configured": bool(entry["profile"].get("api_key"))}}

    def put_config(self, service_id, *, expected_revision, profile):
        self._service(service_id)
        with self._lock:
            state = self._read()
            previous = state["services"].get(service_id, {"revision": 0, "profile": {}})
            _need(type(expected_revision) is int and expected_revision >= 0
                  and isinstance(profile, dict) and not set(profile) - _PROFILE_FIELDS
                  and ("clear_api_key" not in profile or type(profile["clear_api_key"]) is bool))
            _need(expected_revision == previous["revision"], "ai_config_revision_changed", 409)
            merged = {"enabled": True, "temperature": 0.2, "max_tokens": 2048, "timeout_seconds": 45,
                      **previous["profile"], **profile}
            _need(type(merged["enabled"]) is bool)
            stored = dict(id=service_id, name=_text(merged.get("name"), 120), base_url=_base_url(merged.get("base_url")),
                          model=_text(merged.get("model"), 200), enabled=merged["enabled"],
                          temperature=_number(merged["temperature"], 0, 2),
                          max_tokens=_number(merged["max_tokens"], 1, 131072, integer=True),
                          timeout_seconds=_number(merged["timeout_seconds"], 1, 300))
            _need("api_key" not in profile or isinstance(profile["api_key"], str) and len(profile["api_key"]) <= 8192)
            supplied = profile.get("api_key", "").strip()
            _need(all(33 <= ord(char) <= 126 for char in supplied) and not (profile.get("clear_api_key") and supplied))
            _need(not previous["profile"].get("api_key") or stored["base_url"] == previous["profile"]["base_url"]
                  or supplied or profile.get("clear_api_key"), "ai_destination_changed", 422)
            secret = supplied or ("" if profile.get("clear_api_key") else previous["profile"].get("api_key", ""))
            secrets = self._secrets(state) + [supplied]
            _need(not any(key and any(key in str(value) for value in stored.values()) for key in secrets))
            _need(not supplied or not any(supplied in str(entry["profile"][key])
                  for entry in state["services"].values() for key in _PUBLIC))
            state["services"][service_id] = {"revision": previous["revision"] + 1, "profile": {**stored, "api_key": secret}}
            self._write(state)
            return self.get_config(service_id)

    async def _request(self, url, payload, headers, timeout, *, provider=False):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        _need(len(body) <= (65536 if provider or url.endswith("/chat") else 16384), "ai_request_size", 413)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False, timeout=timeout) as client:
                    async with client.stream("POST", url, content=body,
                            headers={"Content-Type": "application/json", "Accept-Encoding": "identity", **headers}) as response:
                        if not response.is_success:
                            status = response.status_code
                            code = ("ai_provider_auth" if status in {401, 403} else "ai_provider_limited" if status == 429
                                    else "ai_provider_failed") if provider else "ai_platform_rejected"
                            raise AIServiceError(code, 502 if provider else status if 400 <= status <= 599 else 502)
                        _need(response.headers.get("content-encoding", "identity") == "identity", "ai_response", 502)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            _need(len(raw) + len(chunk) <= 4194304, "ai_response_size", 502)
                            raw.extend(chunk)
                        return json.loads(raw)
        except AIServiceError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, TimeoutError):
            raise AIServiceError("ai_provider_unavailable" if provider else "ai_platform_unavailable") from None

    def _scope(self, scope, service_id):
        _need(isinstance(scope, dict) and isinstance(scope.get("ticket"), str) and 0 < len(scope["ticket"]) <= 12288
              and _scope_identifier(scope.get("request_id")) and _scope_identifier(scope.get("operation"))
              and _scope_identifier(scope.get("permission")) and _facts(scope.get("facts")), "ai_access_scope", 403)
        return dict(service_id=service_id, request_id=scope["request_id"], operation=scope["operation"],
                    permission=scope["permission"], facts=deepcopy(scope["facts"]))

    def _route(self, route, service_id):
        _need(isinstance(route, dict) and {"service_id", "source", "binding_revision", "module_config_revision", "platform_config_digest"} <= set(route)
              and route.get("service_id") == service_id
              and route.get("source") in self._service(service_id)["supported_sources"]
              and type(route.get("binding_revision")) is int and route["binding_revision"] >= 1
              and (route.get("module_config_revision") is None and isinstance(route.get("platform_config_digest"), str)
                   and re.fullmatch(r"[0-9a-f]{64}", route["platform_config_digest"]) if route.get("source") == "platform" else
                   type(route.get("module_config_revision")) is int and route["module_config_revision"] >= 0
                   and route.get("platform_config_digest") is None),
              "ai_route_response", 502)
        return route

    async def _own_chat(self, service_id, messages, revision):
        with self._lock:
            entry = self._read()["services"].get(service_id)
            _need(entry and entry["revision"] == revision, "ai_config_revision_changed", 409)
            profile, secrets = entry["profile"], self._secrets()
            _need(profile["enabled"], "ai_service_disabled", 409)
        payload = await self._request(profile["base_url"] + "/chat/completions", dict(
            model=profile["model"], messages=_redact(messages, secrets), temperature=profile["temperature"],
            max_tokens=profile["max_tokens"], stream=False),
            {"Authorization": "Bearer " + profile["api_key"]} if profile.get("api_key") else {}, profile["timeout_seconds"], provider=True)
        try:
            choice, message = payload["choices"][0], payload["choices"][0]["message"]
            _need(isinstance(message, dict) and (message.get("content") is None or isinstance(message["content"], str))
                  and not message.get("tool_calls"), "ai_response", 502)
            result = dict(content=message.get("content") or "", tool_calls=[], model=payload.get("model") or profile["model"])
            if choice.get("finish_reason") in _FINISH:
                result["finish_reason"] = choice["finish_reason"]
            if isinstance(payload.get("usage"), dict):
                result["usage"] = {key: value for key, value in payload["usage"].items()
                                   if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int and value >= 0}
            _need(self.get_config(service_id)["revision"] == revision, "ai_config_revision_changed", 409)
            return _redact(_result(result), secrets + self._secrets())
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AIServiceError("ai_response", 502) from None

    async def chat_with_receipt(self, *, service_id, messages, scope=None):
        service = self._service(service_id)
        _messages(messages)
        if self._platform is None:
            _need("module" in service["supported_sources"], "ai_source_unsupported", 409)
            revision = self.get_config(service_id)["revision"]
            result = await self._own_chat(service_id, deepcopy(messages), revision)
            return {"result": result, "route": dict(service_id=service_id, source="module", binding_revision=None, module_config_revision=revision, platform_config_digest=None)}
        body = self._scope(scope, service_id)
        ticket = scope["ticket"]
        headers = {"X-Platform-Access": ticket}
        messages = _redact(deepcopy(messages), [ticket])
        route = self._route(await self._request(self._platform + "/api/v1/ai/resolve", body, headers, 15), service_id)
        selected = {**body, "binding_revision": route["binding_revision"], "module_config_revision": route["module_config_revision"], "platform_config_digest": route["platform_config_digest"]}
        if route["source"] == "platform":
            answer = await self._request(self._platform + "/api/v1/ai/chat", {**selected, "messages": _redact(messages, self._secrets())}, headers, 310)
            self._route(answer, service_id)
            _need(all(answer[key] == route[key] for key in ("source", "binding_revision", "module_config_revision", "platform_config_digest")), "ai_binding_revision_changed", 409)
            result = _result(answer.get("result"))
        else:
            result = await self._own_chat(service_id, deepcopy(messages), route["module_config_revision"])
        after = self._route(await self._request(self._platform + "/api/v1/ai/validate", selected, headers, 15), service_id)
        _need(all(after[key] == route[key] for key in ("source", "binding_revision", "module_config_revision", "platform_config_digest")), "ai_binding_revision_changed", 409)
        if route["source"] == "module":
            _need(self.get_config(service_id)["revision"] == route["module_config_revision"], "ai_config_revision_changed", 409)
        return {"result": _result(_redact(result, self._secrets() + [ticket])),
                "route": dict(service_id=service_id, source=route["source"], binding_revision=route["binding_revision"],
                              module_config_revision=route["module_config_revision"], platform_config_digest=route["platform_config_digest"])}

    async def chat(self, **options):
        return (await self.chat_with_receipt(**options))["result"]

    async def validate(self, *, service_id, route, scope=None):
        """Recheck immediately before a domain commit; no distributed atomicity."""
        self._service(service_id)
        if self._platform is None:
            _need(isinstance(route, dict) and route.get("service_id") == service_id and route.get("source") == "module"
                  and route.get("binding_revision") is None and route.get("platform_config_digest") is None and type(route.get("module_config_revision")) is int
                  and self.get_config(service_id)["revision"] == route["module_config_revision"], "ai_config_revision_changed", 409)
            return deepcopy(route)
        self._route(route, service_id)
        selected = {**self._scope(scope, service_id), "binding_revision": route["binding_revision"],
                    "module_config_revision": route["module_config_revision"], "platform_config_digest": route["platform_config_digest"]}
        after = self._route(await self._request(self._platform + "/api/v1/ai/validate", selected,
                                               {"X-Platform-Access": scope["ticket"]}, 15), service_id)
        _need(all(after[key] == route[key] for key in ("source", "binding_revision", "module_config_revision", "platform_config_digest")), "ai_binding_revision_changed", 409)
        if route["source"] == "module":
            _need(self.get_config(service_id)["revision"] == route["module_config_revision"], "ai_config_revision_changed", 409)
        return {key: after[key] for key in ("service_id", "source", "binding_revision", "module_config_revision", "platform_config_digest")}

    async def test_connection(self, service_id, expected_revision):
        self._service(service_id)
        revision = self.get_config(service_id)["revision"]
        _need(type(expected_revision) is int and expected_revision == revision, "ai_config_revision_changed", 409)
        started = time.monotonic()
        result = await self._own_chat(service_id, [{"role": "user", "content": "This is a synthetic connection test. Reply with CONNECTION_OK only."}], revision)
        return dict(ok=True, message="连接成功，已收到供应商响应。", latency_ms=round((time.monotonic() - started) * 1000),
                    model=result["model"], response_preview=result["content"][:240])

    async def control(self, service_id, method, action, body=None):
        """Only invoke after authenticating the service Bearer; never a public API."""
        if action == "config" and method == "GET":
            return self.get_config(service_id)
        if action == "config" and method == "PUT":
            _need(isinstance(body, dict) and set(body) == {"expected_revision", "profile"})
            return self.put_config(service_id, **body)
        if action == "test" and method == "POST":
            _need(isinstance(body, dict) and set(body) == {"expected_revision"})
            return await self.test_connection(service_id, body["expected_revision"])
        raise AIServiceError("ai_control_not_found", 404)
