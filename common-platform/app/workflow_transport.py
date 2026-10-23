"""Bounded workflow/file proxy to a capability's administered service.

The caller chooses the capability from an installed flow, never from a browser
URL. This layer stores neither business objects nor upload/job state. Existing
invoke envelopes are unchanged. Workflows installs trusted local manifest
factories through the explicit adapter seam; no local file store or job executor
is implicitly installed by this transport.
"""

from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from .errors import PlatformError, require
from .identity import effective_roles, refresh_actor
from .job_contract import validate_operation_result, validate_page_input, validate_reference
from .remote_services import RemoteServiceError


MAX_CHUNK_BYTES = 1024 * 1024
MAX_JSON_REQUEST_BYTES = 256_000
MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_LIST_BYTES = 128 * 1024
MAX_PROJECTION_BYTES = 1024 * 1024
MAX_PAGE_BYTES = 512 * 1024
OPERATION_RESPONSE_LIMITS = {
    "objects.list": MAX_LIST_BYTES,
    "objects.read": MAX_PROJECTION_BYTES,
    "objects.detail": MAX_PAGE_BYTES,
}
OPERATIONS = frozenset(
    {
        "objects.list",
        "objects.read",
        "objects.detail",
        "uploads.create",
        "uploads.status",
        "uploads.complete",
        "uploads.abort",
        "jobs.status",
        "jobs.result",
        "jobs.cancel",
    }
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LocalWorkflowContext:
    """Factory context for trusted, installed Python modules; not browser input."""

    sessions: Any
    data_dir: Path
    module_id: str


def _json_bytes(value, limit, *, response=False):
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise PlatformError(
            "workflow_contract", "流程数据不符合 JSON 契约。", 502 if response else 422
        ) from None
    require(
        len(encoded) <= limit,
        "workflow_response_size" if response else "workflow_request_size",
        "流程响应超过允许大小。" if response else "流程请求超过允许大小。",
        502 if response else 413,
    )
    return encoded


class WorkflowTransport:
    def __init__(self, runtime, *, local_adapters=None):
        self.runtime = runtime
        self.local_adapters = dict(local_adapters or {})

    @staticmethod
    def _binding(spec):
        return tuple(
            spec.get(key)
            for key in (
                "id",
                "version",
                "module_id",
                "module_revision",
                "service_id",
                "binding_revision",
            )
        )

    def _authorize(self, actor, capability_id, expected=None):
        with self.runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
        spec = self.runtime.registry.get(capability_id, current)
        if expected is not None:
            require(
                self._binding(spec) == expected,
                "workflow_binding_changed",
                "流程能力或服务连接已经变化，请重新读取。",
                409,
            )
        availability = self.runtime.availability(spec)
        require(
            availability["available"],
            "workflow_unavailable",
            "流程服务当前不可用，请稍后重试。",
            503,
        )
        return current, spec

    @staticmethod
    def _principal(actor):
        # Never forward browser context, usernames, permissions or credentials.
        return {
            "id": actor["id"],
            "role": actor["role"],
            "roles": sorted(effective_roles(actor)),
            "name": actor["name"],
            "organization_id": actor.get("organization_id"),
            "team_ids": list(actor.get("team_ids", [])),
            "authorization_revision": actor.get("authorization_revision", 1),
        }

    def _safe_json(self, value, limit, *, response=False):
        encoded = _json_bytes(value, limit, response=response)
        strings = []

        def collect(item, depth=0):
            require(
                depth <= 64,
                "workflow_depth",
                "流程数据嵌套超过允许深度。",
                502 if response else 422,
            )
            if isinstance(item, str):
                strings.append(item)
            elif isinstance(item, dict):
                for key, child in item.items():
                    collect(key, depth + 1)
                    collect(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    collect(child, depth + 1)

        collect(value)
        # Compare actual values, not JSON-escaped text: a credential containing
        # quotes/backslashes must still be detected before publication. One scan
        # avoids repeatedly decrypting configuration for every table cell.
        text = "\0".join(strings)
        require(
            self.runtime.redact(text) == text,
            "workflow_secret",
            "流程数据包含服务凭据，未继续处理。",
            502 if response else 422,
        )
        return encoded

    def _snapshot(self, spec):
        manager = self.runtime.remote_services
        require(manager is not None, "workflow_adapter_missing", "流程服务适配器未配置。", 501)
        try:
            # Reuse the administrator-owned connection and credential store. The
            # transport intentionally has no separate configuration or tokens.
            item, token, _ = manager._snapshot(spec["service_id"])
            manager._same_revision(item, spec["binding_revision"])
            require(item["enabled"], "workflow_disabled", "流程服务已停用。", 503)
            return item, token
        except RemoteServiceError as exc:
            raise PlatformError(exc.code, exc.message, 503) from None

    def _check_snapshot(self, spec):
        manager = self.runtime.remote_services
        try:
            with manager._lock:
                item = manager._find(manager._read(), spec["service_id"])
                manager._same_revision(item, spec["binding_revision"])
                require(item["enabled"], "workflow_disabled", "流程服务已停用，响应未发布。", 503)
        except RemoteServiceError as exc:
            raise PlatformError(exc.code, exc.message, 409) from None

    async def request(self, actor, capability_id, operation, payload):
        require(
            isinstance(operation, str) and operation in OPERATIONS,
            "workflow_operation",
            "未声明的流程操作。",
            422,
        )
        require(isinstance(payload, dict), "workflow_input", "流程输入必须为对象。", 422)
        if operation in {"objects.list", "objects.detail"}:
            validate_page_input(operation, payload)
        if operation != "objects.list":
            validate_reference(payload.get("object_ref"))
        actor, spec = self._authorize(actor, capability_id)
        request_id = uuid.uuid4().hex
        body = {
            "protocol_version": "1.0",
            "request_id": request_id,
            "context": {"actor": self._principal(actor)},
            "input": deepcopy(payload),
        }
        content = self._safe_json(body, MAX_JSON_REQUEST_BYTES)
        binding = self._binding(spec)
        if not spec.get("service_id"):
            data = await self._local(
                spec,
                "request",
                actor=deepcopy(body["context"]["actor"]),
                capability_id=capability_id,
                operation=operation,
                payload=deepcopy(payload),
                request_id=request_id,
            )
        else:
            headers = {"Content-Type": "application/json"}
            if self.runtime.access.module_registered(spec["module_id"]):
                headers["X-Platform-Access"] = self.runtime.access_bridge.issue(
                    actor, spec, request_id, operation
                )
            data = await self._remote(
                spec, "POST", "workflow/" + operation, request_id, content, headers
            )
            require(
                not headers.get("X-Platform-Access")
                or headers["X-Platform-Access"] not in json.dumps(data, ensure_ascii=False),
                "workflow_credential",
                "服务响应包含授权凭据。",
                502,
            )
        self._authorize(actor, capability_id, binding)
        data = self._data(data)
        if operation in OPERATION_RESPONSE_LIMITS:
            self._safe_json(data, OPERATION_RESPONSE_LIMITS[operation], response=True)
        validate_operation_result(
            operation,
            data,
            payload.get("object_ref"),
            payload=payload,
            legacy_list=not spec.get("service_id"),
        )
        return data

    async def upload_chunk(
        self, actor, capability_id, object_ref, upload_id, index, content, *, sha256=None
    ):
        require(
            isinstance(upload_id, str) and IDENTIFIER.fullmatch(upload_id),
            "upload_id",
            "上传会话编号无效。",
            422,
        )
        require(
            type(index) is int and 0 <= index <= 100_000, "upload_index", "文件分块编号无效。", 422
        )
        require(
            isinstance(content, bytes) and 0 < len(content) <= MAX_CHUNK_BYTES,
            "upload_chunk_size",
            "文件分块为空或超过 1 MiB。",
            413,
        )
        if sha256 is not None:
            require(
                isinstance(sha256, str) and SHA256.fullmatch(sha256),
                "upload_digest",
                "文件分块摘要无效。",
                422,
            )
            require(
                hashlib.sha256(content).hexdigest() == sha256,
                "upload_digest",
                "文件分块摘要不匹配。",
                422,
            )
        validate_reference(object_ref)
        actor, spec = self._authorize(actor, capability_id)
        request_id, binding = uuid.uuid4().hex, self._binding(spec)
        context = {"protocol_version": "1.0", "actor": self._principal(actor)}
        if object_ref is not None:
            context["object_ref"] = deepcopy(object_ref)
        encoded_context = self._safe_json(context, 16_000)
        if not spec.get("service_id"):
            data = await self._local(
                spec,
                "upload_chunk",
                actor=deepcopy(context["actor"]),
                capability_id=capability_id,
                object_ref=deepcopy(object_ref),
                upload_id=upload_id,
                index=index,
                content=content,
                sha256=sha256,
                request_id=request_id,
            )
        else:
            headers = {
                "Content-Type": "application/octet-stream",
                "X-Request-ID": request_id,
                "X-Platform-Context": base64.b64encode(encoded_context).decode("ascii"),
            }
            if sha256:
                headers["X-Chunk-SHA256"] = sha256
            if self.runtime.access.module_registered(spec["module_id"]):
                headers["X-Platform-Access"] = self.runtime.access_bridge.issue(
                    actor, spec, request_id, "uploads.chunk"
                )
            data = await self._remote(
                spec,
                "PUT",
                f"workflow/uploads/{upload_id}/chunks/{index}",
                request_id,
                content,
                headers,
            )
            require(
                not headers.get("X-Platform-Access")
                or headers["X-Platform-Access"] not in json.dumps(data, ensure_ascii=False),
                "workflow_credential",
                "服务响应包含授权凭据。",
                502,
            )
        self._authorize(actor, capability_id, binding)
        data = self._data(data)
        validate_operation_result("uploads.chunk", data, object_ref)
        return data

    async def _local(self, spec, method, **kwargs):
        adapter = self.local_adapters.get(spec["module_id"])
        handler = getattr(adapter, method, None) if adapter is not None else None
        require(
            callable(handler),
            "workflow_adapter_missing",
            "此本地模块尚未安装文件或任务适配器。",
            501,
        )
        try:
            output = handler(**kwargs)
            return await output if inspect.isawaitable(output) else output
        except PlatformError:
            raise
        except Exception:
            raise PlatformError(
                "workflow_local_failed", "本地流程适配器未能完成操作。", 500
            ) from None

    def _data(self, data):
        require(isinstance(data, dict), "workflow_response", "流程服务响应不符合契约。", 502)
        self._safe_json(data, MAX_JSON_RESPONSE_BYTES, response=True)

        def without_inline_files(value):
            if isinstance(value, dict):
                return "content_base64" not in value and all(
                    without_inline_files(item) for item in value.values()
                )
            if isinstance(value, list):
                return all(without_inline_files(item) for item in value)
            return True

        require(
            without_inline_files(data),
            "workflow_inline_file",
            "流程响应只能包含文件引用，不能内联文件字节。",
            502,
        )
        return deepcopy(data)

    async def _remote(self, spec, method, endpoint, request_id, content, headers):
        item, token = self._snapshot(spec)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", **headers}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            async with asyncio.timeout(item["timeout_seconds"]):
                async with httpx.AsyncClient(
                    transport=self.runtime.remote_services._transport,
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
                        await self._raise_status(response)
                        require(
                            response.headers.get("content-encoding", "identity").lower()
                            == "identity",
                            "workflow_response_encoding",
                            "流程响应不能使用压缩编码。",
                            502,
                        )
                        declared = response.headers.get("content-length")
                        require(
                            declared is None
                            or (declared.isdigit() and int(declared) <= MAX_JSON_RESPONSE_BYTES),
                            "workflow_response_size",
                            "流程响应超过允许大小。",
                            502,
                        )
                        received = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            require(
                                len(received) + len(chunk) <= MAX_JSON_RESPONSE_BYTES,
                                "workflow_response_size",
                                "流程响应超过允许大小。",
                                502,
                            )
                            received.extend(chunk)
        except (httpx.TimeoutException, TimeoutError):
            raise PlatformError(
                "workflow_timeout", "流程服务请求超时；请先查询当前状态，勿假定操作未执行。", 503
            ) from None
        except httpx.HTTPError:
            raise PlatformError(
                "workflow_connection", "无法连接流程服务，请稍后重试。", 503
            ) from None
        try:

            def invalid_constant(_):
                raise ValueError()

            def unique_fields(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value

            payload = json.loads(
                received, parse_constant=invalid_constant, object_pairs_hook=unique_fields
            )
        except (ValueError, UnicodeError, RecursionError):
            raise PlatformError(
                "workflow_response", "流程服务响应不符合 JSON 契约。", 502
            ) from None
        require(
            isinstance(payload, dict)
            and set(payload) == {"protocol_version", "request_id", "data"}
            and payload["protocol_version"] == "1.0"
            and payload["request_id"] == request_id,
            "workflow_response",
            "流程服务响应标识或版本不匹配。",
            502,
        )
        self._check_snapshot(spec)
        return self._data(payload["data"])

    @staticmethod
    async def _raise_status(response):
        status = response.status_code
        if status == 200:
            return
        if 300 <= status < 400:
            raise PlatformError("workflow_redirect", "流程服务重定向已被拒绝。", 502)
        if status == 202:
            raise PlatformError(
                "workflow_response", "流程适配操作必须返回当前状态；异步工作应返回任务引用。", 502
            )
        errors = {
            401: ("workflow_auth", "流程服务连接认证失败。", 502),
            403: ("workflow_forbidden", "当前身份无权操作该业务对象。", 403),
            404: ("workflow_not_found", "对象不存在或当前身份不可见。", 404),
            409: ("workflow_conflict", "业务对象或上传状态已变化，请重新读取。", 409),
            413: ("workflow_input_size", "文件或输入超过业务服务限制。", 413),
            422: ("workflow_validation", "文件或输入未通过业务校验。", 422),
            429: ("workflow_rate_limit", "流程服务请求过多，请稍后重试。", 503),
            503: ("workflow_unavailable", "流程服务暂不可用，请稍后重试。", 503),
        }
        error = errors.get(status, ("workflow_failed", "流程服务未能完成操作。", 502))
        if status == 409:
            # Only an allowlisted selector is used; remote prose/details remain
            # private, bounded even for an untrusted streaming error response.
            known = {
                "revision_conflict": (
                    "revision_conflict",
                    "业务对象版本已变化，请重新读取后修改。",
                    409,
                ),
                "state_conflict": ("state_conflict", "当前业务状态不允许此操作。", 409),
                "idempotency_conflict": ("idempotency_conflict", "请求编号已用于不同内容。", 409),
            }
            try:
                async with asyncio.timeout(1):
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        if len(raw) + len(chunk) > 4096:
                            break
                        raw.extend(chunk)
                    else:
                        value = json.loads(raw)
                        code = (
                            value.get("error", {}).get("code")
                            if isinstance(value, dict) and isinstance(value.get("error"), dict)
                            else None
                        )
                        if isinstance(code, str):
                            error = known.get(code, error)
            except (ValueError, UnicodeError, RecursionError, httpx.HTTPError, TimeoutError):
                pass
        raise PlatformError(*error)
