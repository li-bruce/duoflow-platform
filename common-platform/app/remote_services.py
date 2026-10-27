"""Versioned connections to explicitly administered remote capability services.

This module owns transport/configuration only: callers must enforce administrator
permissions for management and capability/user permissions for invocation. It does
not import remote Python, follow URLs supplied by manifests, or execute UI code.
The token adapter reuses ModelGateway's local DPAPI/Fernet implementation in its
own remote-secrets directory; it does not inspect model profiles. The Fernet key
and configuration reside on the same machine, so protection against whole-directory
access still depends on filesystem permissions. Atomic writes support one API
process, not concurrent independently running configuration writers.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .model_gateway import ModelGateway, ModelGatewayError
from .errors import PlatformError
from .module_contract import normalize_surfaces
from .service_monitor import ServiceMonitor
from .workspace_contract import (
    ROLE_IDS,
    REQUEST_SCHEMA,
    MAX_INVOKE_BYTES,
    MAX_WORKSPACE_RESPONSE_BYTES,
)
from .registration_contract import (
    CHECKS,
    digest as contract_digest,
    make_offer,
    validate_confirmation,
    validate_registration,
    validate_receipt,
)


class RemoteServiceError(Exception):
    """Safe public error; no response bodies, URLs, inputs or tokens in messages."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class _LocalTokenStore:
    """Narrow adapter around the existing local encryption primitive, not profiles."""

    def __init__(self, directory: Path):
        self._crypto = ModelGateway(directory)

    def encrypt(self, token: str) -> dict:
        try:
            return self._crypto._encrypt(token)
        except ModelGatewayError:
            raise RemoteServiceError(
                "remote_secret_unavailable", "远程服务凭据存储不可用，请检查本机目录权限。"
            ) from None

    def decrypt(self, secret: dict | None) -> str:
        try:
            return self._crypto._decrypt(secret)
        except ModelGatewayError:
            raise RemoteServiceError(
                "remote_secret_unavailable", "无法解密远程服务凭据，请在本机重新配置。"
            ) from None


def _redact(value: Any, tokens: list[str]) -> Any:
    if isinstance(value, str):
        for token in sorted(set(tokens), key=len, reverse=True):
            if token:
                value = value.replace(token, "【凭据已隐藏】")
        return value
    if isinstance(value, dict):
        return {_redact(key, tokens): _redact(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, tokens) for item in value]
    return value


class RemoteServiceManager:
    # The module SDK returns only error.code/message. Only these fixed codes may
    # select public diagnostic wording; supplier/module messages stay private.
    _AI_FAILURES = {
        "ai_provider_limited": "模块 AI 供应商请求受限或额度不足，请稍后重试并检查供应商额度。",
        "ai_provider_auth": "模块 AI 供应商拒绝认证，请检查模块自有 API 配置。",
        "ai_provider_failed": "模块 AI 供应商未能完成请求，请检查模型名称、参数与供应商状态。",
        "ai_provider_unavailable": "模块 AI 供应商连接失败或超时，请检查网络、地址与超时设置。",
        "ai_platform_rejected": "平台 AI 服务拒绝本次请求，请检查当前来源、授权、模型配置与服务状态。",
        "ai_platform_unavailable": "模块暂时无法连接平台 AI 服务，请检查平台连接与服务状态。",
        "ai_disabled": "该模块的 AI 服务尚未启用，请由管理员配置 AI 来源。",
        "ai_service_disabled": "模块自有 AI 配置已禁用，请由管理员检查配置。",
        "ai_binding_revision_changed": "AI 来源已变化，本次结果未采纳，请刷新后重新办理。",
        "ai_config_revision_changed": "模块 AI 配置已变化，本次结果未采纳，请刷新后重新办理。",
        "ai_platform_config_changed": "平台 AI 配置已变化，本次结果未采纳，请刷新后重新办理。",
        "model_timeout": "平台模型请求超时，请检查模型超时设置或稍后重试。",
        "model_rate_limited": "平台模型请求受限或额度不足，请检查供应商额度或稍后重试。",
        "model_auth_failed": "平台模型拒绝认证，请由管理员检查平台 API 配置。",
        "model_connection_failed": "平台模型连接失败，请检查模型地址、网络与服务状态。",
        "model_provider_error": "平台模型供应商未能完成请求，请检查模型名称、参数与服务状态。",
        "model_disabled": "已选择的平台模型已禁用，请由管理员检查 AI 来源。",
        "model_not_configured": "平台模型尚未配置，请由管理员保存并选择可用模型。",
    }
    _FIELDS = {
        "id",
        "name",
        "base_url",
        "token",
        "clear_token",
        "timeout_seconds",
        "expected_revision",
    }
    _PUBLIC = (
        "id",
        "name",
        "base_url",
        "timeout_seconds",
        "enabled",
        "revision",
        "discovered_revision",
        "last_health",
    )
    _MAX_RESPONSE = 4 * 1024 * 1024
    _MAX_MANIFEST = 512_000
    _MAX_REQUEST = 512_000

    def __init__(
        self, data_dir: Path, transport: httpx.AsyncBaseTransport | None = None, *, clock=None
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "remote-services.json"
        self._tokens = _LocalTokenStore(self.data_dir / "remote-secrets")
        self._lock = threading.RLock()
        self._transport = transport
        self._monitor = ServiceMonitor(self, clock=clock)
        # Large modern schemas must not be revalidated for each heartbeat or
        # each capability in a registry read. Cache by content, never by time.
        self._read_digest = None
        self._read_state = None
        self._validated_manifests = set()
        self._admitting = set()
        self.admission_probe = None

    @staticmethod
    def _invalid():
        return RemoteServiceError(
            "invalid_remote_config", "远程服务连接格式或参数无效，请检查后重试。"
        )

    @staticmethod
    def _string(value, limit, *, allow_empty=False):
        if (
            not isinstance(value, str)
            or len(value) > limit
            or (not allow_empty and not value.strip())
            or any(ord(c) < 32 for c in value)
        ):
            raise RemoteServiceManager._invalid()
        return value.strip()

    @staticmethod
    def _url(value):
        try:
            return ModelGateway._base_url(value)
        except ModelGatewayError:
            raise RemoteServiceError(
                "invalid_remote_url",
                "远程服务请使用 HTTPS；HTTP 仅支持 localhost 或回环地址，地址不能含凭据、查询参数或片段。",
            ) from None

    @staticmethod
    def _timeout(value):
        try:
            return ModelGateway._number(value, 1, 300)
        except ModelGatewayError:
            raise RemoteServiceManager._invalid() from None

    @staticmethod
    def _positive_integer(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "services": []}
        try:
            raw = self.path.read_bytes()
            digest = hashlib.sha256(raw).digest()
            if digest == self._read_digest and self._read_state is not None:
                return copy.deepcopy(self._read_state)
            state = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(state, dict)
                or set(state) != {"version", "services"}
                or state["version"] != 1
                or not isinstance(state["services"], list)
            ):
                raise ValueError()
            ids = set()
            for item in state["services"]:
                allowed = set(self._PUBLIC) | {"secret", "manifest", "registration", "admission"}
                if (
                    not isinstance(item, dict)
                    or set(item) - allowed
                    or set(self._PUBLIC) - set(item)
                    or not isinstance(item["id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item["id"])
                    or item["id"] in ids
                    or not isinstance(item["enabled"], bool)
                    or not self._positive_integer(item["revision"])
                ):
                    raise ValueError()
                self._string(item["name"], 120)
                self._url(item["base_url"])
                self._timeout(item["timeout_seconds"])
                if (
                    item["discovered_revision"] is not None
                    and item["discovered_revision"] != item["revision"]
                ):
                    raise ValueError()
                if item.get("manifest") is not None:
                    manifest_digest = hashlib.sha256(
                        json.dumps(item["manifest"], sort_keys=True, ensure_ascii=False).encode(
                            "utf-8"
                        )
                    ).digest()
                    if manifest_digest not in self._validated_manifests:
                        self._validate_manifest(item["manifest"])
                        if len(self._validated_manifests) >= 128:
                            self._validated_manifests.clear()
                        self._validated_manifests.add(manifest_digest)
                    if item["discovered_revision"] != item["revision"]:
                        raise ValueError()
                elif item["enabled"] or item["discovered_revision"] is not None:
                    raise ValueError()
                secret = item.get("secret")
                if secret is not None and (
                    not isinstance(secret, dict)
                    or set(secret) != {"scheme", "ciphertext"}
                    or secret["scheme"] not in {"dpapi", "fernet"}
                    or not isinstance(secret["ciphertext"], str)
                ):
                    raise ValueError()
                health = item["last_health"]
                if health is not None and (
                    not isinstance(health, dict)
                    or set(health) != {"ok", "message", "latency_ms"}
                    or not isinstance(health["ok"], bool)
                    or not isinstance(health["message"], str)
                    or not isinstance(health["latency_ms"], int)
                ):
                    raise ValueError()
                item.setdefault("registration", None)
                item.setdefault("admission", None)
                report = item["admission"]
                if report is not None:
                    validate_receipt(report, item)
                # Old persisted publications have no acceptance evidence. Keep
                # their preview, but never reload them into the live registry.
                if report is None or not report["passed"]:
                    item["enabled"] = False
                ids.add(item["id"])
            self._read_digest, self._read_state = digest, copy.deepcopy(state)
            return state
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RemoteServiceError,
            RecursionError,
            SchemaError,
            ValidationError,
            PlatformError,
        ):
            raise RemoteServiceError(
                "remote_config_corrupt", "远程服务配置无法读取，请由管理员检查或恢复备份。"
            ) from None

    def _write(self, state):
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".remote-services-", dir=self.data_dir)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            raise RemoteServiceError(
                "remote_config_write_failed", "远程服务配置保存失败，原配置未被替换。"
            ) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _find(state, service_id):
        item = next((item for item in state["services"] if item["id"] == service_id), None)
        if item is None:
            raise RemoteServiceError("remote_service_not_found", "远程服务连接不存在。")
        return item

    def _all_tokens(self, state, *, replacing=None, allow_unavailable=False):
        result = []
        for item in state["services"]:
            if item.get("secret"):
                try:
                    result.append(self._tokens.decrypt(item["secret"]))
                except RemoteServiceError:
                    if item["id"] != replacing and not allow_unavailable:
                        raise
        return result

    def _public(self, item):
        manifest = copy.deepcopy(item.get("manifest"))
        return {
            **{name: copy.deepcopy(item[name]) for name in RemoteServiceManager._PUBLIC},
            "token_configured": bool(item.get("secret")),
            "manifest": manifest,
            "module": manifest["module"] if manifest else None,
            "capabilities": manifest["capabilities"] if manifest else [],
            "registration": copy.deepcopy(item.get("registration")),
            "admission": copy.deepcopy(item.get("admission")),
            "monitor": self._monitor.status(item["id"], item["revision"]),
        }

    @property
    def secrets_storage(self):
        with self._lock:
            items = self._read()["services"]
            if any(item.get("secret", {}).get("scheme") == "fernet" for item in items):
                return "fernet-local-file"
            return "windows-dpapi" if os.name == "nt" else "fernet-local-file"

    def list_services(self):
        with self._lock:
            return {"items": [self._public(item) for item in self._read()["services"]]}

    def _monitor_targets(self):
        with self._lock:
            return [item["id"] for item in self._read()["services"]]

    def _monitor_revision(self, service_id):
        with self._lock:
            return self._find(self._read(), service_id)["revision"]

    def monitor_status(self, service_id):
        with self._lock:
            item = self._find(self._read(), service_id)
            return self._monitor.status(service_id, item["revision"])

    def check_available(self, service_id):
        state = self.monitor_status(service_id)["state"]
        if state == "offline":
            raise RemoteServiceError(
                "remote_offline", "远程服务连续健康检查失败，请等待恢复后再执行。"
            )
        if state == "stale":
            raise RemoteServiceError(
                "remote_stale", "远程服务健康状态已过期，请先检查连接后再执行。"
            )
        # Unknown permits an explicit first call; it is not reported as healthy.
        # Publication/actor permissions are separate gates in the runtime.

    async def monitor_loop(self, stop_event):
        await self._monitor.run(stop_event)

    def redact(self, text: str) -> str:
        with self._lock:
            return _redact(text, self._all_tokens(self._read()))

    def save_connection(self, payload, *, require_revision=False):
        """Save trusted configuration; HTTP updates must supply expected_revision.

        HTTP callers set require_revision=True; omission remains supported for
        existing trusted maintenance callers.
        Whenever supplied, the comparison and write share the manager lock.
        """
        if not isinstance(payload, dict) or set(payload) - self._FIELDS:
            raise self._invalid()
        if "expected_revision" in payload and not self._positive_integer(
            payload["expected_revision"]
        ):
            raise self._invalid()
        with self._lock:
            state = self._read()
            service_id = payload.get("id") or uuid.uuid4().hex
            if not isinstance(service_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,80}", service_id
            ):
                raise self._invalid()
            previous = next((item for item in state["services"] if item["id"] == service_id), {})
            if previous and require_revision and "expected_revision" not in payload:
                raise RemoteServiceError(
                    "remote_revision_required", "更新已有远程连接必须提供当前修订，请刷新后重试。"
                )
            if "expected_revision" in payload:
                if not previous:
                    raise RemoteServiceError(
                        "remote_revision_changed", "远程连接已变化或不存在，请刷新后重试。"
                    )
                self._same_revision(previous, payload["expected_revision"])
            merged = {"timeout_seconds": 60, **previous, **payload}
            item = {
                "id": service_id,
                "name": self._string(merged.get("name"), 120),
                "base_url": self._url(merged.get("base_url")),
                "timeout_seconds": self._timeout(merged["timeout_seconds"]),
            }
            supplied = payload.get("token")
            if supplied is not None and (not isinstance(supplied, str) or len(supplied) > 8192):
                raise self._invalid()
            supplied = supplied.strip() if supplied else ""
            if any(not 33 <= ord(c) <= 126 for c in supplied):
                raise self._invalid()
            clear = payload.get("clear_token", False)
            if not isinstance(clear, bool) or (clear and supplied):
                raise self._invalid()
            if (
                previous.get("secret")
                and item["base_url"] != previous["base_url"]
                and not (clear or supplied)
            ):
                raise RemoteServiceError(
                    "remote_destination_changed",
                    "服务地址已变化。请重新填写适用于新地址的凭据，或明确清除凭据后保存。",
                )
            # Explicit credential replacement/clearing must remain possible after
            # an account change makes several old DPAPI blobs unreadable. This
            # exception is management-only: redact/invoke retain strict defaults.
            tokens = self._all_tokens(
                state,
                replacing=service_id if clear or supplied else None,
                allow_unavailable=bool(clear or supplied),
            )
            if supplied:
                tokens.append(supplied)
            if _redact(item, tokens) != item:
                raise RemoteServiceError(
                    "invalid_remote_config", "凭据只能填写在远程服务凭据字段中。"
                )
            if supplied and any(
                _redact(self._public(old), [supplied]) != self._public(old)
                for old in state["services"]
                if old["id"] != service_id
            ):
                raise RemoteServiceError(
                    "invalid_remote_config", "凭据不能与其他远程连接的公开内容重合。"
                )
            old_token = ""
            if previous.get("secret"):
                try:
                    old_token = self._tokens.decrypt(previous["secret"])
                except RemoteServiceError:
                    if not (clear or supplied):
                        raise
                    old_token = None
            new_token = supplied or ("" if clear else old_token)
            changed = (
                not previous
                or any(
                    item[name] != previous.get(name)
                    for name in ("name", "base_url", "timeout_seconds")
                )
                or new_token != old_token
            )
            secret = previous.get("secret")
            if clear:
                secret = None
            elif supplied and supplied != old_token:
                secret = self._tokens.encrypt(supplied)
            if secret:
                item["secret"] = secret
            item.update(
                {
                    "revision": (
                        previous.get("revision", 0) + 1 if changed else previous["revision"]
                    ),
                    "enabled": False if changed else previous["enabled"],
                    "discovered_revision": None if changed else previous["discovered_revision"],
                    "manifest": None if changed else previous.get("manifest"),
                    "registration": None if changed else previous.get("registration"),
                    "admission": None if changed else previous.get("admission"),
                    "last_health": None if changed else previous["last_health"],
                }
            )
            index = next(
                (index for index, old in enumerate(state["services"]) if old["id"] == service_id),
                None,
            )
            if index is None:
                state["services"].append(item)
            else:
                state["services"][index] = item
            self._write(state)
            return self._public(item)

    @staticmethod
    def _validate_manifest(manifest):
        def invalid():
            raise RemoteServiceError(
                "remote_invalid_manifest",
                "远程服务清单不符合平台支持的 Manifest 1.0/1.1/1.2 契约，请检查版本、字段、Schema 与双端呈现声明。",
            )

        def fields(obj, required, optional=()):
            if (
                not isinstance(obj, dict)
                or set(required) - set(obj)
                or set(obj) - set(required) - set(optional)
            ):
                invalid()

        def string(value, limit=200, empty=False):
            if (
                not isinstance(value, str)
                or len(value) > limit
                or (not empty and not value.strip())
                or any(ord(c) < 32 for c in value)
            ):
                invalid()

        def identifier(value):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,120}", value):
                invalid()

        visited = 0

        def refs(node, depth=0):
            nonlocal visited
            visited += 1
            if depth > 40 or visited > 20000:
                invalid()
            if isinstance(node, dict):
                for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    if key in node and (
                        not isinstance(node[key], str) or not node[key].startswith("#")
                    ):
                        invalid()
                for child in node.values():
                    refs(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    refs(child, depth + 1)

        try:
            fields(manifest, {"protocol_version", "module", "capabilities"}, {"surfaces"})
            if manifest["protocol_version"] not in {"1.0", "1.1", "1.2"}:
                invalid()
            modern = manifest["protocol_version"] == "1.2"
            if manifest["protocol_version"] in {"1.1", "1.2"} and manifest.get("surfaces") is None:
                invalid()
            module = manifest["module"]
            fields(module, {"id", "version", "name", "description"})
            identifier(module["id"])
            string(module["version"], 40)
            string(module["name"], 120)
            string(module["description"], 3000, True)
            capabilities = manifest["capabilities"]
            if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
                invalid()
            ids = set()
            for cap in capabilities:
                fields(
                    cap,
                    {
                        "id",
                        "version",
                        "title",
                        "description",
                        "kind",
                        "input_schema",
                        "output_schema",
                        "roles",
                        "execution",
                        "presentation",
                    },
                    {"result"},
                )
                identifier(cap["id"])
                if cap["id"] in ids:
                    invalid()
                ids.add(cap["id"])
                string(cap["version"], 40)
                string(cap["title"], 200)
                string(cap["description"], 3000, True)
                if cap["kind"] not in {"query", "command"}:
                    invalid()
                roles = cap["roles"]
                if (
                    not isinstance(roles, list)
                    or not roles
                    or any(
                        role not in (ROLE_IDS if modern else ["admin", "member"]) for role in roles
                    )
                    or len(set(roles)) != len(roles)
                ):
                    invalid()
                fields(cap["execution"], {"mode", "confirmation"})
                if cap["execution"]["mode"] != "background" or not isinstance(
                    cap["execution"]["confirmation"], bool
                ):
                    invalid()
                fields(cap["presentation"], {"page", "conversation"})
                for host in ("page", "conversation"):
                    fields(cap["presentation"][host], {"renderer"})
                    if cap["presentation"][host]["renderer"] not in (
                        {"text-result", "json", "workspace"} if modern else {"text-result", "json"}
                    ):
                        invalid()
                for name in ("input_schema", "output_schema"):
                    schema = cap[name]
                    if not isinstance(schema, dict) or schema.get("type") != "object":
                        invalid()
                    refs(schema)
                    Draft202012Validator.check_schema(schema)
                if "result" in cap:
                    result = cap["result"]
                    fields(
                        result, {"resource_type", "title"}, {"text_artifact", "title_from_input"}
                    )
                    identifier(result["resource_type"])
                    string(result["title"], 200)
                    if "title_from_input" in result:
                        string(result["title_from_input"], 120)
                    if "text_artifact" in result:
                        artifact = result["text_artifact"]
                        fields(artifact, {"field", "name"})
                        string(artifact["field"], 120)
                        string(artifact["name"], 180)
                        if any(c in artifact["name"] for c in "/\\") or artifact["name"] in {
                            ".",
                            "..",
                        }:
                            invalid()
            normalize_surfaces(
                module["id"],
                capabilities,
                manifest.get("surfaces"),
                allow_embedded=True,
                allow_skills=modern,
            )
            # Preserve legacy 1.0 manifests byte-for-field for cached-contract
            # comparison. Registration callers normalize their surfaces as needed.
            # Protocol 1.1 changes discovery only; invoke envelopes remain 1.0.
            return copy.deepcopy(manifest)
        except (SchemaError, TypeError, ValueError, KeyError, RecursionError):
            invalid()

    def _snapshot(self, service_id):
        with self._lock:
            state = self._read()
            item = copy.deepcopy(self._find(state, service_id))
            token = self._tokens.decrypt(item.get("secret"))
            tokens = self._all_tokens(state)
            return item, token, tokens

    @staticmethod
    def _same_revision(item, revision):
        if not RemoteServiceManager._positive_integer(revision) or item["revision"] != revision:
            raise RemoteServiceError(
                "remote_revision_changed",
                "远程连接或启停状态已变化，请刷新并核对当前修订后再执行。",
            )

    async def _known_ai_failure(self, response):
        """Decode a bounded selector only; never retain a remote message/body."""
        try:
            async with asyncio.timeout(1):
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=1024):
                    if len(content) + len(chunk) > 4096:
                        return None
                    content.extend(chunk)
                payload = json.loads(content)
                error = payload.get("error") if isinstance(payload, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                if isinstance(code, str) and code in self._AI_FAILURES:
                    return "remote_" + code, self._AI_FAILURES[code]
        except (ValueError, UnicodeError, RecursionError, httpx.HTTPError, TimeoutError):
            pass
        return None

    async def _request(
        self,
        item,
        token,
        method,
        endpoint,
        *,
        body=None,
        limit=None,
        access_ticket=None,
        observation_ticket=None,
    ):
        headers = {"Accept": "application/json"}
        if access_ticket:
            headers["X-Platform-Access"] = access_ticket
        if observation_ticket:
            headers["X-DuoFlow-Observation"] = observation_ticket
        if token:
            headers["Authorization"] = "Bearer " + token
        content = None
        if body is not None:
            try:
                content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, RecursionError):
                raise RemoteServiceError("remote_invalid_request", "远程请求格式无效。") from None
            request_limit = (
                2 * self._MAX_MANIFEST + 8192
                if endpoint == "handshake"
                else (
                    MAX_INVOKE_BYTES if body.get("protocol_version") == "1.1" else self._MAX_REQUEST
                )
            )
            if len(content) > request_limit:
                raise RemoteServiceError(
                    "remote_request_too_large", "远程请求超过允许大小，请缩小输入内容。"
                )
            headers["Content-Type"] = "application/json"
        try:
            async with asyncio.timeout(item["timeout_seconds"]):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(
                        item["timeout_seconds"], connect=min(10, item["timeout_seconds"])
                    ),
                ) as client:
                    async with client.stream(
                        method,
                        item["base_url"] + "/platform/v1/" + endpoint,
                        headers=headers,
                        content=content,
                    ) as response:
                        status = response.status_code
                        if 300 <= status < 400:
                            raise RemoteServiceError(
                                "remote_redirect_blocked",
                                "远程服务返回重定向，请管理员直接配置最终服务地址。",
                            )
                        if status == 202:
                            raise RemoteServiceError(
                                "remote_async_unsupported",
                                "远程服务返回异步任务，本版仅支持在当前请求中完成的服务响应。",
                            )
                        if endpoint == "invoke" and status in {429, 500, 501, 502, 503, 504}:
                            failure = await self._known_ai_failure(response)
                            if failure is not None:
                                raise RemoteServiceError(*failure)
                        modern_invoke = (
                            endpoint == "invoke"
                            and isinstance(body, dict)
                            and body.get("protocol_version") == "1.1"
                        )
                        if modern_invoke:
                            business_errors = {
                                403: (
                                    "remote_forbidden",
                                    "当前身份无权执行此业务操作，请核对角色和对象范围。",
                                ),
                                404: (
                                    "remote_not_found",
                                    "目标对象不存在或当前身份不可见，请重新查询。",
                                ),
                                413: (
                                    "remote_input_too_large",
                                    "输入内容超过远程服务允许大小，请缩小文件或数据包后重新提交。",
                                ),
                                422: (
                                    "remote_validation",
                                    "输入未通过远程业务校验，请检查必填字段、取值和文件内容。",
                                ),
                                503: ("remote_unavailable", "远程业务服务暂不可用，请稍后重试。"),
                            }
                            if status in business_errors:
                                raise RemoteServiceError(*business_errors[status])
                            if status == 409:
                                conflict = (
                                    "remote_conflict",
                                    "远程业务报告状态或版本冲突，请重新查询记录后核对本次操作。",
                                )
                                known_conflicts = {
                                    "revision_conflict": (
                                        "remote_revision_conflict",
                                        "数据版本已变化，请重新读取当前记录后再提交。",
                                    ),
                                    "state_conflict": (
                                        "remote_state_conflict",
                                        "当前业务状态不允许此操作，请刷新记录并核对办理步骤。",
                                    ),
                                    "idempotency_conflict": (
                                        "remote_idempotency_conflict",
                                        "请求标识已用于不同内容，请核对本次提交，勿修改参数后复用原请求标识。",
                                    ),
                                }
                                # The optional code is a selector into fixed public
                                # wording. Never retain or echo remote error text.
                                # Limit both bytes and time even for malformed errors.
                                try:
                                    async with asyncio.timeout(1):
                                        error_bytes, oversized = bytearray(), False
                                        async for chunk in response.aiter_bytes(chunk_size=1024):
                                            if len(error_bytes) + len(chunk) > 4096:
                                                oversized = True
                                                break
                                            error_bytes.extend(chunk)
                                        if not oversized:
                                            payload = json.loads(error_bytes)
                                            error = (
                                                payload.get("error")
                                                if isinstance(payload, dict)
                                                else None
                                            )
                                            code = (
                                                error.get("code")
                                                if isinstance(error, dict)
                                                else None
                                            )
                                            if isinstance(code, str) and code in known_conflicts:
                                                conflict = known_conflicts[code]
                                            elif isinstance(code, str) and code in self._AI_FAILURES:
                                                conflict = ("remote_" + code, self._AI_FAILURES[code])
                                except (
                                    ValueError,
                                    UnicodeError,
                                    RecursionError,
                                    httpx.HTTPError,
                                    TimeoutError,
                                ):
                                    pass
                                raise RemoteServiceError(*conflict)
                        if endpoint == "invoke" and status == 409:
                            failure = await self._known_ai_failure(response)
                            if failure is not None:
                                raise RemoteServiceError(*failure)
                        if endpoint.startswith("ai/services/") and status in {409, 413, 422, 503, 504}:
                            # Fixed AI controls are configuration transport, not
                            # invocations. Preserve safe status semantics without
                            # retaining the module's error body or credentials.
                            errors = {
                                409: ("ai_module_revision_conflict", "模块 API 配置或状态已变化，请刷新后重试。"),
                                413: ("ai_module_config_size", "模块 API 配置超过允许大小。"),
                                422: ("ai_module_config_invalid", "模块未接受 API 配置，请检查字段和参数。"),
                                503: ("ai_module_unavailable", "模块 AI 服务暂不可用。"),
                                504: ("ai_module_timeout", "模块 AI 请求超时，未自动重试。"),
                            }
                            code, message = errors[status]
                            raise PlatformError(code, message, status)
                        if status in {401, 403}:
                            raise RemoteServiceError(
                                "remote_auth_failed", "远程服务拒绝认证，请检查连接凭据与服务权限。"
                            )
                        if status == 409:
                            raise RemoteServiceError(
                                "remote_idempotency_conflict",
                                "远程服务报告请求冲突，请检查相同请求标识的内容是否一致。",
                            )
                        if status == 429:
                            raise RemoteServiceError(
                                "remote_rate_limited", "远程服务请求过多，请稍后手动重试。"
                            )
                        if status != 200:
                            raise RemoteServiceError(
                                "remote_service_failed",
                                "远程服务未能完成请求，请检查服务状态与协议。",
                            )
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > (limit or self._MAX_RESPONSE):
                                raise RemoteServiceError(
                                    "remote_response_too_large",
                                    "远程响应超过允许大小，请由服务端缩小返回内容。",
                                )
                            chunks.append(chunk)
                        raw = b"".join(chunks)
        except (httpx.TimeoutException, TimeoutError):
            raise RemoteServiceError(
                "remote_timeout", "远程服务请求超时，未自动重试；可检查服务记录后手动重试。"
            ) from None
        except (httpx.RequestError, httpx.InvalidURL):
            raise RemoteServiceError(
                "remote_connection_failed", "无法连接远程服务，请检查地址、服务状态与网络。"
            ) from None
        try:

            def invalid_constant(_):
                raise ValueError()

            return json.loads(raw, parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise RemoteServiceError("remote_invalid_json", "远程服务未返回有效 JSON。") from None

    async def discover(self, service_id, *, before_commit=None):
        item, token, tokens = self._snapshot(service_id)
        response = await self._request(item, token, "GET", "manifest", limit=self._MAX_MANIFEST)
        if _redact(response, tokens) != response:
            raise RemoteServiceError(
                "remote_secret_echo", "远程能力清单包含连接凭据，未保存该清单。"
            )
        manifest = self._validate_manifest(response)
        with self._lock:
            state = self._read()
            current = self._find(state, service_id)
            self._same_revision(current, item["revision"])
            if before_commit is not None:
                before_commit()
            # Credentials on another connection may have changed while discovery
            # was waiting. Validate against the current set before persistence.
            if _redact(response, self._all_tokens(state)) != response:
                raise RemoteServiceError(
                    "remote_secret_echo", "远程能力清单包含连接凭据，未保存该清单。"
                )
            if current.get("manifest") is not None and current["manifest"] != manifest:
                current["revision"] += 1
                current["enabled"] = False
                current["last_health"] = None
                current["registration"], current["admission"] = None, None
            current["manifest"], current["discovered_revision"] = manifest, current["revision"]
            self._write(state)
            return self._public(current)

    async def _expect_rejection(self, item, token, method, endpoint, statuses):
        """Check a fixed interface without reading a body or invoking business data.

        Missing/wrong credentials and an empty envelope must fail before dispatch.
        Redirects, 202 and successful empty invocations are never accepted.
        """
        try:
            async with asyncio.timeout(min(item["timeout_seconds"], 10)):
                async with httpx.AsyncClient(
                    transport=self._transport, trust_env=False, follow_redirects=False
                ) as client:
                    async with client.stream(
                        method,
                        item["base_url"] + "/platform/v1/" + endpoint,
                        headers={"Authorization": "Bearer " + token} if token else {},
                        json={} if method == "POST" else None,
                    ) as response:
                        if response.status_code not in statuses:
                            raise RemoteServiceError(
                                "remote_admission_interface",
                                "接口未按接入标准拒绝无身份或无效请求。",
                            )
        except (httpx.HTTPError, TimeoutError):
            raise RemoteServiceError(
                "remote_admission_connection", "接入检查无法完成接口连接。"
            ) from None

    async def admit(self, service_id, expected_revision, *, host_check=None, before_commit=None):
        """Allow one admission per connection; cancellation always releases its slot."""
        with self._lock:
            if service_id in self._admitting:
                raise RemoteServiceError(
                    "remote_admission_busy", "该服务正在进行接入检查，请等待当前检查完成。"
                )
            self._admitting.add(service_id)
        try:
            return await self._admit(
                service_id, expected_revision, host_check=host_check, before_commit=before_commit
            )
        finally:
            with self._lock:
                self._admitting.discard(service_id)

    async def _admit(self, service_id, expected_revision, *, host_check, before_commit):
        """Perform read-only bilateral admission and atomically store every result.

        No business command is called. Network work never holds the manager lock;
        changed connections and revoked administrators cannot commit stale proof.
        A failed recheck revokes publication and invalidates queued invocations.
        """
        item, token, tokens = self._snapshot(service_id)
        self._same_revision(item, expected_revision)
        if not item.get("manifest"):
            raise RemoteServiceError("remote_not_discovered", "请先发现当前连接的能力声明。")
        registration = None
        checks = [
            {"id": key, "title": title, "passed": False, "code": "not_checked"}
            for key, title in CHECKS
        ]
        stage = 0
        try:
            if not token:
                raise RemoteServiceError(
                    "remote_admission_credentials", "接入验收需要配置服务连接凭据。"
                )
            fresh = await self._request(item, token, "GET", "manifest", limit=self._MAX_MANIFEST)
            self._validate_manifest(fresh)
            if fresh != item["manifest"]:
                raise RemoteServiceError(
                    "remote_manifest_changed", "服务声明已经变化，请重新发现后验收。"
                )
            checks[stage].update(passed=True, code="ok")
            stage += 1
            document = await self._request(
                item, token, "GET", "registration", limit=self._MAX_MANIFEST
            )
            if _redact(document, tokens) != document:
                raise RemoteServiceError(
                    "remote_secret_echo", "接入声明含有连接凭据，未保存该声明。"
                )
            validate_registration(document, fresh)
            registration = document
            checks[stage].update(passed=True, code="ok")
            stage += 1
            endpoints = [("GET", name) for name in ("manifest", "health", "registration")]
            if registration["registration_version"] == "1.1":
                endpoints += [("GET", "module-graph"), ("GET", "flow-exports")]
                for name, field in (
                    ("module-graph", "module_graph"),
                    ("flow-exports", "flow_exports"),
                ):
                    actual = await self._request(item, token, "GET", name, limit=self._MAX_MANIFEST)
                    if actual != registration[field]:
                        raise ValueError("registration_document_changed")
            endpoints += [("POST", name) for name in ("invoke", "handshake")]
            endpoints += [
                ("POST", "workflow/" + name) for name in registration["workflow_operations"]
            ]
            for method, endpoint in endpoints:
                for credential in ("", "invalid-" + uuid.uuid4().hex):
                    await self._expect_rejection(item, credential, method, endpoint, {401, 403})
            checks[stage].update(passed=True, code="ok")
            stage += 1
            health = await self._request(item, token, "GET", "health", limit=8192)
            if (
                not isinstance(health, dict)
                or not (health.get("ok") is True or health.get("status") in ("ok", "healthy"))
                or health.get("ok") is False
                or health.get("status") in ("error", "failed", "unhealthy", "offline", "degraded")
            ):
                raise RemoteServiceError("remote_admission_health", "服务健康状态未通过。")
            checks[stage].update(passed=True, code="ok")
            stage += 1
            for method, endpoint in endpoints:
                if method == "POST":
                    await self._expect_rejection(item, token, method, endpoint, {400, 422})
            checks[stage].update(passed=True, code="ok")
            stage += 1
            offer = make_offer(fresh, registration, uuid.uuid4().hex)
            if registration["registration_version"] == "1.1":
                if self.admission_probe is None:
                    raise ValueError("registration_runtime_unavailable")
                offer["runtime_probe"] = self.admission_probe.begin_probe(
                    service_id, expected_revision
                )
            answer = await self._request(item, token, "POST", "handshake", body=offer, limit=32768)
            validate_confirmation(answer, offer)
            if registration["registration_version"] == "1.1":
                self.admission_probe.require_probe(offer["runtime_probe"])
            checks[stage].update(passed=True, code="ok")
            stage += 1
            if host_check is not None:
                host_check(registration)
            elif registration["permissions"]["mode"] != "capability_roles" or any(
                registration["host_requirements"].values()
            ):
                raise RemoteServiceError(
                    "remote_admission_host", "需要在平台内检查权限定义与共享呈现依赖。"
                )
            checks[stage].update(passed=True, code="ok")
        except (
            RemoteServiceError,
            ValidationError,
            SchemaError,
            PlatformError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
        ) as error:
            # Remote text and library validation details can include business
            # data. Persist only a bounded code chosen by this implementation.
            safe_code = (
                error.code if isinstance(error, RemoteServiceError) else "remote_admission_contract"
            )
            checks[stage]["code"] = safe_code
        passed = all(check["passed"] for check in checks)
        with self._lock:
            state = self._read()
            current = self._find(state, service_id)
            self._same_revision(current, expected_revision)
            if before_commit is not None:
                before_commit()
            if (
                registration is not None
                and _redact(registration, self._all_tokens(state)) != registration
            ):
                raise RemoteServiceError(
                    "remote_secret_echo", "接入声明含有连接凭据，未保存该声明。"
                )
            contract_changed = (
                current.get("registration") is not None and current["registration"] != registration
            )
            if current["enabled"] and (not passed or contract_changed):
                current["revision"] += 1
                current["discovered_revision"] = current["revision"]
                current["enabled"] = False
            current["registration"] = registration
            current["admission"] = {
                "version": "1.0",
                "revision": current["revision"],
                "passed": passed,
                "checked_at": self._monitor.clock(),
                "contract_digest": contract_digest(
                    {"manifest": current["manifest"], "registration": registration}
                ),
                "checks": checks,
            }
            self._write(state)
            return self._public(current)

    def require_admitted(self, item):
        """Publication is a separate operation; old or incomplete proof cannot pass."""
        report = item.get("admission")
        if (
            not report
            or not report["passed"]
            or report["revision"] != item["revision"]
            or (not item["enabled"] and self._monitor.clock() - report["checked_at"] > 300)
        ):
            raise RemoteServiceError(
                "remote_admission_required",
                "全部接入检查通过后，才能启用发布；验收超过五分钟请重新检查。",
            )

    def invalidate_host_admission(self, service_id, expected_revision):
        """Revoke persisted acceptance when installed dependencies changed at restart."""
        with self._lock:
            state = self._read()
            item = self._find(state, service_id)
            self._same_revision(item, expected_revision)
            if not item.get("admission"):
                return
            if item["enabled"]:
                item["revision"] += 1
                item["discovered_revision"] = item["revision"]
            item["enabled"] = False
            item["admission"]["revision"] = item["revision"]
            item["admission"]["passed"] = False
            item["admission"]["checks"][-1].update(passed=False, code="remote_admission_host")
            self._write(state)

    async def health(self, service_id):
        return await self._monitor.probe(service_id)

    async def _probe_health(self, service_id, *, expected_revision=None):
        with self._lock:
            # A broken credential belonging to another connection must not stop
            # this service's heartbeat; only its own token is needed here.
            item = copy.deepcopy(self._find(self._read(), service_id))
            if expected_revision is not None:
                self._same_revision(item, expected_revision)
        started = self._monitor.clock()
        error = None
        try:
            token = self._tokens.decrypt(item.get("secret"))
            response = await self._request(
                {**item, "timeout_seconds": ServiceMonitor.TIMEOUT_SECONDS},
                token,
                "GET",
                "health",
                limit=self._MAX_MANIFEST,
            )
            if (
                not isinstance(response, dict)
                or ("ok" in response and not isinstance(response["ok"], bool))
                or ("status" in response and not isinstance(response["status"], str))
            ):
                raise RemoteServiceError(
                    "remote_invalid_health", "远程健康接口未返回明确且有效的健康状态。"
                )
            healthy = response.get("ok") is True or response.get("status") in ("ok", "healthy")
            unhealthy = response.get("ok") is False or response.get("status") in (
                "error",
                "failed",
                "unhealthy",
                "offline",
                "degraded",
            )
            if not (healthy or unhealthy):
                raise RemoteServiceError(
                    "remote_invalid_health", "远程健康接口未返回明确且有效的健康状态。"
                )
            ok = healthy and not unhealthy
            message = "远程服务连接正常。" if ok else "远程服务报告当前不可用。"
        except RemoteServiceError as exc:
            error, ok, message = exc, False, exc.message
        except Exception:
            error = RemoteServiceError(
                "remote_health_failed", "远程服务健康检查失败，请检查服务状态。"
            )
            ok, message = False, error.message
        result = {
            "ok": ok,
            "message": message,
            "latency_ms": max(0, round((self._monitor.clock() - started) * 1000)),
        }
        with self._lock:
            state = self._read()
            current = self._find(state, service_id)
            # A response for old credentials or an old endpoint cannot overwrite
            # the current revision's unknown/healthy/degraded state.
            self._same_revision(current, item["revision"])
            self._monitor.record(service_id, item["revision"], **result)
            current["last_health"] = result
            self._write(state)
        if error is not None:
            raise error from None
        return result

    def set_enabled(self, service_id, enabled, expected_revision):
        if not isinstance(enabled, bool):
            raise self._invalid()
        with self._lock:
            state = self._read()
            item = self._find(state, service_id)
            self._same_revision(item, expected_revision)
            if enabled and (
                item.get("manifest") is None or item["discovered_revision"] != item["revision"]
            ):
                raise RemoteServiceError(
                    "remote_not_discovered", "请先发现并核对当前连接版本的能力，再启用服务。"
                )
            if enabled:
                self.require_admitted(item)
            if item["enabled"] == enabled:
                return self._public(item)
            item["enabled"] = enabled
            previous_revision = item["revision"]
            item["revision"] += 1
            # Publication changes invalidate old invocations without requiring
            # discovery of an unchanged manifest again.
            if item.get("manifest") is not None:
                item["discovered_revision"] = item["revision"]
            if item.get("admission") is not None:
                item["admission"]["revision"] = item["revision"]
            self._write(state)
            self._monitor.rebind_revision(service_id, previous_revision, item["revision"])
            return self._public(item)

    @staticmethod
    def _binding(item):
        return {
            "service_id": item["id"],
            "revision": item["revision"],
            "manifest": copy.deepcopy(item.get("manifest")),
        }

    def enabled_bindings(self):
        with self._lock:
            return [self._binding(item) for item in self._read()["services"] if item["enabled"]]

    def get_binding(self, service_id):
        with self._lock:
            return self._binding(self._find(self._read(), service_id))

    def observation_graph(self, service_id, revision):
        """Return only the admitted graph attached to this exact service revision."""
        with self._lock:
            item = self._find(self._read(), service_id)
            self._same_revision(item, revision)
            document = item.get("registration") or {}
            if document.get("registration_version") != "1.1":
                return None
            self.require_admitted(item)
            return copy.deepcopy(document["module_graph"])

    async def invoke(
        self, service_id, revision, request, *, access_ticket=None, observation_ticket=None
    ):
        item, token, tokens = self._snapshot(service_id)
        if access_ticket:
            tokens = [*tokens, access_ticket]
        if observation_ticket:
            tokens = [*tokens, observation_ticket]
        self._same_revision(item, revision)
        if not item["enabled"]:
            raise RemoteServiceError("remote_disabled", "远程服务已停用，本次未发送请求。")
        modern = item["manifest"]["protocol_version"] == "1.2"
        required = {
            "protocol_version",
            "request_id",
            "capability_id",
            "capability_version",
            "input",
            "context",
        }
        if (
            not isinstance(request, dict)
            or set(request) != required
            or request["protocol_version"] != ("1.1" if modern else "1.0")
            or not isinstance(request["request_id"], str)
            or not 1 <= len(request["request_id"]) <= 160
            or any(ord(c) < 32 for c in request["request_id"])
            or not isinstance(request["input"], dict)
        ):
            raise RemoteServiceError("remote_invalid_request", "远程请求格式无效。")
        cap = next(
            (
                cap
                for cap in item["manifest"]["capabilities"]
                if cap["id"] == request["capability_id"]
            ),
            None,
        )
        if cap is None or cap["version"] != request["capability_version"]:
            raise RemoteServiceError(
                "remote_capability_changed", "远程能力或版本与已发现清单不一致，请刷新后重试。"
            )
        context = request["context"]
        if modern:
            if (
                not Draft202012Validator(REQUEST_SCHEMA).is_valid(request)
                or context["actor"]["role"] not in context["actor"]["roles"]
                or (
                    not access_ticket
                    and not set(context["actor"]["roles"]).intersection(cap["roles"])
                )
            ):
                raise RemoteServiceError(
                    "remote_invalid_request", "远程调用的统一身份上下文无效或未授权。"
                )
        elif (
            not isinstance(context, dict)
            or set(context) != {"workspace_id", "actor"}
            or not isinstance(context["workspace_id"], str)
            or not context["workspace_id"]
            or not isinstance(context["actor"], dict)
            or set(context["actor"]) != {"id", "role"}
            or not isinstance(context["actor"]["id"], str)
            or not context["actor"]["id"]
            or context["actor"]["role"] not in cap["roles"]
        ):
            raise RemoteServiceError(
                "remote_invalid_request", "远程请求上下文无效或当前身份无权使用该能力。"
            )
        if _redact(request, tokens) != request:
            raise RemoteServiceError(
                "remote_secret_in_input", "请求包含已配置的远程服务凭据，请移除后重试。"
            )
        response = await self._request(
            item,
            token,
            "POST",
            "invoke",
            body=request,
            limit=MAX_WORKSPACE_RESPONSE_BYTES if modern else self._MAX_RESPONSE,
            **({"access_ticket": access_ticket} if access_ticket else {}),
            **({"observation_ticket": observation_ticket} if observation_ticket else {}),
        )
        if (
            not isinstance(response, dict)
            or set(response)
            != {
                "protocol_version",
                "request_id",
                "capability_id",
                "capability_version",
                "summary",
                "data",
            }
            or any(
                response.get(key) != request[key]
                for key in ("protocol_version", "request_id", "capability_id", "capability_version")
            )
            or not isinstance(response["summary"], str)
            or len(response["summary"]) > 12000
            or not isinstance(response["data"], dict)
        ):
            raise RemoteServiceError(
                "remote_invalid_response", "远程响应不符合协议，或请求标识与能力版本不匹配。"
            )
        # Configuration may have changed while the remote request was running.
        # Return no stale result; no attempt is made to undo external service work.
        with self._lock:
            current = self._find(self._read(), service_id)
            self._same_revision(current, revision)
            if not current["enabled"]:
                raise RemoteServiceError(
                    "remote_disabled", "远程服务在执行期间被停用，本次结果未发布。"
                )
            tokens = list(set(tokens + self._all_tokens(self._read())))
        return _redact(response, tokens)
