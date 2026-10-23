"""Private, file-backed OpenAI-compatible model profiles.

Only explicitly supplied profiles are read; no environment/provider credentials are
discovered. Windows DPAPI binds secrets to the current Windows account. Fernet is
the fallback: its 0600 key file lives beside the encrypted configuration, so this
protects a copied config file, not an attacker who can read the entire directory.
Windows installations using that fallback must also restrict the directory ACL.
This local implementation assumes one API process; writes are thread-safe and
atomic, but multiple processes require an external transaction/locking store.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import ipaddress
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken


class ModelGatewayError(Exception):
    """An intentionally safe error; never include provider bodies or credentials."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI unavailable")

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    blob_ptr = ctypes.POINTER(DataBlob)
    if decrypt:
        call = crypt32.CryptUnprotectData
        call.argtypes = [
            blob_ptr,
            ctypes.c_void_p,
            blob_ptr,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            blob_ptr,
        ]
        description = None
    else:
        call = crypt32.CryptProtectData
        call.argtypes = [
            blob_ptr,
            ctypes.c_wchar_p,
            blob_ptr,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            blob_ptr,
        ]
        description = "Common Platform model credential"
    call.restype = ctypes.c_int
    if not call(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(destination)):
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(destination.pbData, ctypes.c_void_p))


def _redact(value: Any, secrets: list[str]) -> Any:
    """Redact exact credentials in all returned text, including tool arguments."""
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                # Credentials are ASCII; a Unicode marker cannot itself match
                # another configured credential such as the literal "SECRET".
                value = value.replace(secret, "【密钥已隐藏】")
        return value
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {_redact(key, secrets): _redact(item, secrets) for key, item in value.items()}
    return value


class ModelGateway:
    """Persistent profiles and real, non-streaming chat completions.

    ``transport`` is an HTTPX transport seam for deterministic HTTP mock tests.
    Saving, listing, disabling, and deleting never contact a model provider.
    """

    _FIELDS = {
        "id",
        "name",
        "base_url",
        "model",
        "api_key",
        "clear_api_key",
        "enabled",
        "temperature",
        "max_tokens",
        "timeout_seconds",
        "is_default",
    }
    _PUBLIC = (
        "id",
        "name",
        "base_url",
        "model",
        "enabled",
        "temperature",
        "max_tokens",
        "timeout_seconds",
    )
    _MAX_RESPONSE = 4 * 1024 * 1024
    _FINISH_REASONS = frozenset(
        {"stop", "length", "max_tokens", "tool_calls", "content_filter", "function_call"}
    )

    def __init__(self, data_dir: Path, *, transport: httpx.AsyncBaseTransport | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "model-profiles.json"
        self._key_path = self.data_dir / "model-profiles.fernet.key"
        self._lock = threading.RLock()
        self._transport = transport

    @staticmethod
    def _invalid() -> ModelGatewayError:
        return ModelGatewayError("invalid_model_config", "模型配置格式或参数无效，请检查后重试。")

    @staticmethod
    def _base_url(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 2048
            or any(character.isspace() or ord(character) < 32 for character in value)
            or "\\" in value
            or "?" in value
            or "#" in value
        ):
            raise ModelGateway._invalid()
        try:
            parsed = urlsplit(value)
            port = parsed.port
            host = parsed.hostname
            if (
                parsed.scheme not in {"http", "https"}
                or not host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError()
            if parsed.scheme == "http":
                loopback = host.lower() == "localhost"
                try:
                    loopback = loopback or ipaddress.ip_address(host).is_loopback
                except ValueError:
                    pass
                if not loopback:
                    raise ModelGatewayError(
                        "invalid_model_url",
                        "远程模型地址请使用 HTTPS；HTTP 仅支持 localhost 或回环地址。",
                    )
            # Percent escapes in authority can obscure userinfo or host delimiters.
            if "%" in parsed.netloc:
                raise ValueError()
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        except ModelGatewayError:
            raise
        except (ValueError, TypeError):
            raise ModelGateway._invalid() from None

    @staticmethod
    def _string(value: Any, limit: int) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or any(ord(character) < 32 for character in value)
        ):
            raise ModelGateway._invalid()
        return value.strip()

    @staticmethod
    def _number(
        value: Any, minimum: float, maximum: float, *, integer: bool = False
    ) -> float | int:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= maximum
            or (integer and not isinstance(value, int))
        ):
            raise ModelGateway._invalid()
        return int(value) if integer else float(value)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "default_profile_id": None, "profiles": []}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or state.get("version") != 1
                or not isinstance(state.get("profiles"), list)
            ):
                raise ValueError()
            ids = set()
            for profile in state["profiles"]:
                if (
                    not isinstance(profile, dict)
                    or set(self._PUBLIC) - profile.keys()
                    or not isinstance(profile["id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile["id"])
                    or profile["id"] in ids
                    or "api_key" in profile
                    or not isinstance(profile["enabled"], bool)
                ):
                    raise ValueError()
                self._base_url(profile["base_url"])
                self._string(profile["name"], 120)
                self._string(profile["model"], 200)
                self._number(profile["temperature"], 0, 2)
                self._number(profile["max_tokens"], 1, 131072, integer=True)
                self._number(profile["timeout_seconds"], 1, 300)
                secret = profile.get("secret")
                if secret is not None and (
                    not isinstance(secret, dict)
                    or secret.get("scheme") not in {"dpapi", "fernet"}
                    or not isinstance(secret.get("ciphertext"), str)
                ):
                    raise ValueError()
                ids.add(profile["id"])
            self._repair_default(state)
            return state
        except (OSError, ValueError, TypeError, KeyError, ModelGatewayError):
            raise ModelGatewayError(
                "model_config_corrupt", "模型配置文件无法读取，请由管理员检查或恢复备份。"
            ) from None

    @staticmethod
    def _repair_default(state: dict[str, Any], *, avoid: str | None = None) -> None:
        available = [item["id"] for item in state["profiles"] if item["enabled"]]
        if state.get("default_profile_id") not in available:
            state["default_profile_id"] = next(
                (item for item in available if item != avoid), available[0] if available else None
            )

    def _write(self, state: dict[str, Any]) -> None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".model-profiles-", dir=self.data_dir)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            raise ModelGatewayError(
                "model_config_write_failed", "模型配置保存失败，原配置未被替换。"
            ) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _fernet(self, *, create: bool = False) -> Fernet:
        try:
            if create and not self._key_path.exists():
                new_key = Fernet.generate_key()
                try:
                    descriptor = os.open(
                        self._key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(new_key)
                        output.flush()
                        os.fsync(output.fileno())
                    os.chmod(self._key_path, 0o600)
                except FileExistsError:
                    pass
            return Fernet(self._key_path.read_bytes())
        except (OSError, ValueError):
            raise ModelGatewayError(
                "model_secret_unavailable", "模型密钥存储不可用，请检查本机加密环境与目录权限。"
            ) from None

    def _encrypt(self, value: str) -> dict[str, str]:
        if os.name == "nt":
            try:
                return {
                    "scheme": "dpapi",
                    "ciphertext": base64.b64encode(_dpapi(value.encode())).decode("ascii"),
                }
            except OSError:
                pass
        return {
            "scheme": "fernet",
            "ciphertext": self._fernet(create=True).encrypt(value.encode()).decode("ascii"),
        }

    def _decrypt(self, secret: dict[str, str] | None) -> str:
        if not secret:
            return ""
        try:
            if secret["scheme"] == "dpapi":
                return _dpapi(
                    base64.b64decode(secret["ciphertext"], validate=True), decrypt=True
                ).decode("utf-8")
            return self._fernet().decrypt(secret["ciphertext"].encode("ascii")).decode("utf-8")
        except (OSError, ValueError, KeyError, UnicodeError, InvalidToken):
            raise ModelGatewayError(
                "model_secret_unavailable", "无法解密已保存的模型密钥，请在本机重新配置密钥。"
            ) from None

    @staticmethod
    def _public(profile: dict[str, Any], default_id: str | None, *, admin: bool) -> dict[str, Any]:
        fields = ModelGateway._PUBLIC if admin else ("id", "name", "model")
        result = {key: profile[key] for key in fields}
        result["is_default"] = profile["id"] == default_id
        if admin:
            result["api_key_configured"] = bool(profile.get("secret"))
        return result

    def list_profiles(self, admin: bool = True) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            return {
                "items": [
                    self._public(item, state["default_profile_id"], admin=admin)
                    for item in state["profiles"]
                    if admin or item["enabled"]
                ],
                "default_profile_id": state["default_profile_id"],
            }

    @property
    def configured(self) -> bool:
        return self.list_profiles(admin=False)["default_profile_id"] is not None

    @property
    def secrets_storage(self) -> str:
        """Safe health metadata. Any fallback credential reports the weaker mode."""
        with self._lock:
            profiles = self._read()["profiles"]
            if any(item.get("secret", {}).get("scheme") == "fernet" for item in profiles):
                return "fernet-local-file"
            return "windows-dpapi" if os.name == "nt" else "fernet-local-file"

    def redact(self, text: str) -> str:
        """Remove exact currently configured keys before messages reach storage."""
        with self._lock:
            secrets = []
            for profile in self._read()["profiles"]:
                if profile.get("secret"):
                    secrets.append(self._decrypt(profile["secret"]))
            return _redact(text, secrets)

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - self._FIELDS:
            raise self._invalid()
        with self._lock:
            state = self._read()
            profile_id = payload.get("id") or uuid.uuid4().hex
            if not isinstance(profile_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,80}", profile_id
            ):
                raise self._invalid()
            previous = next((item for item in state["profiles"] if item["id"] == profile_id), {})
            merged = {
                "enabled": True,
                "temperature": 0.2,
                "max_tokens": 2048,
                "timeout_seconds": 45.0,
                **previous,
                **payload,
            }
            for name in ("enabled", "clear_api_key", "is_default"):
                if name in merged and not isinstance(merged[name], bool):
                    raise self._invalid()
            if merged.get("is_default") and not merged["enabled"]:
                raise ModelGatewayError("invalid_model_config", "禁用的模型不能设为默认模型。")
            profile = {
                "id": profile_id,
                "name": self._string(merged.get("name"), 120),
                "base_url": self._base_url(merged.get("base_url")),
                "model": self._string(merged.get("model"), 200),
                "enabled": merged["enabled"],
                "temperature": self._number(merged["temperature"], 0, 2),
                "max_tokens": self._number(merged["max_tokens"], 1, 131072, integer=True),
                "timeout_seconds": self._number(merged["timeout_seconds"], 1, 300),
            }
            supplied = payload.get("api_key")
            if supplied is not None and (not isinstance(supplied, str) or len(supplied) > 8192):
                raise self._invalid()
            supplied = supplied.strip() if supplied else ""
            if any(not 33 <= ord(character) <= 126 for character in supplied):
                raise self._invalid()
            clear = payload.get("clear_api_key", False)
            if clear and supplied:
                raise ModelGatewayError(
                    "invalid_model_config", "请在设置新密钥与清除密钥之间选择一种操作。"
                )
            if (
                previous.get("secret")
                and profile["base_url"] != previous["base_url"]
                and not (supplied or clear)
            ):
                raise ModelGatewayError(
                    "model_destination_changed",
                    "模型服务地址已变化。请重新填写适用于新地址的密钥，或明确清除密钥后保存。",
                )
            # Metadata of a new profile must not echo another profile's credential.
            known_secrets = [supplied] if supplied else []
            for item in state["profiles"]:
                if item.get("secret"):
                    try:
                        known_secrets.append(self._decrypt(item["secret"]))
                    except ModelGatewayError:
                        # Clearing or replacing the one lost credential remains possible.
                        if not ((clear or supplied) and item["id"] == profile_id):
                            raise
            if any(
                secret and any(secret in str(value) for value in profile.values())
                for secret in known_secrets
            ):
                raise ModelGatewayError("invalid_model_config", "密钥只能填写在密钥字段中。")
            if supplied and any(
                supplied in str(item[field])
                for item in state["profiles"]
                if item["id"] != profile_id
                for field in self._PUBLIC
            ):
                raise ModelGatewayError(
                    "invalid_model_config", "密钥不能与其他模型配置中的公开内容重合。"
                )
            secret = None if clear else previous.get("secret")
            if supplied:
                # A credential entered into a label/URL would otherwise leak via GET.
                if any(supplied in str(value) for value in profile.values()):
                    raise ModelGatewayError("invalid_model_config", "密钥只能填写在密钥字段中。")
                secret = self._encrypt(supplied)
            if secret:
                preserved_key = supplied or self._decrypt(secret)
                if any(preserved_key in str(value) for value in profile.values()):
                    raise ModelGatewayError("invalid_model_config", "密钥只能填写在密钥字段中。")
                profile["secret"] = secret
            existing_index = next(
                (index for index, item in enumerate(state["profiles"]) if item["id"] == profile_id),
                None,
            )
            if existing_index is None:
                state["profiles"].append(profile)
            else:
                state["profiles"][existing_index] = profile
            if payload.get("is_default"):
                state["default_profile_id"] = profile_id
            elif payload.get("is_default") is False and state["default_profile_id"] == profile_id:
                state["default_profile_id"] = None
            self._repair_default(
                state, avoid=profile_id if payload.get("is_default") is False else None
            )
            self._write(state)
            return self._public(profile, state["default_profile_id"], admin=True)

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            state = self._read()
            remaining = [item for item in state["profiles"] if item["id"] != profile_id]
            if len(remaining) == len(state["profiles"]):
                raise ModelGatewayError("model_profile_not_found", "模型配置不存在。")
            state["profiles"] = remaining
            self._repair_default(state)
            self._write(state)

    def _snapshot(self, profile_id: str | None) -> tuple[dict[str, Any], str, list[str]]:
        with self._lock:
            state = self._read()
            selected_id = profile_id or state["default_profile_id"]
            if not selected_id:
                raise ModelGatewayError(
                    "model_not_configured", "尚未配置可用模型，请联系管理员配置并测试连接。"
                )
            profile = next((item for item in state["profiles"] if item["id"] == selected_id), None)
            if profile is None:
                raise ModelGatewayError(
                    "model_profile_not_found", "所选模型配置已不存在，请重新选择。"
                )
            if not profile["enabled"]:
                raise ModelGatewayError("model_disabled", "所选模型已禁用，请选择其他模型。")
            key = self._decrypt(profile.get("secret"))
            secrets = [key] if key else []
            for item in state["profiles"]:
                if item["id"] == selected_id or not item.get("secret"):
                    continue
                try:
                    secrets.append(self._decrypt(item["secret"]))
                except ModelGatewayError:
                    # An unavailable credential in another profile does not prevent
                    # an unrelated local, keyless endpoint from being used.
                    pass
            return profile, key, secrets

    async def chat(
        self, messages: list[dict], profile_id: str | None = None, tools: list | None = None
    ) -> dict[str, Any]:
        profile, key, secrets = self._snapshot(profile_id)
        if (
            not isinstance(messages, list)
            or not messages
            or len(messages) > 500
            or any(
                not isinstance(item, dict)
                or item.get("role") not in {"system", "developer", "user", "assistant", "tool"}
                for item in messages
            )
            or (tools is not None and not isinstance(tools, list))
        ):
            raise ModelGatewayError("invalid_model_request", "模型请求格式无效。")
        body: dict[str, Any] = {
            "model": profile["model"],
            "messages": _redact(messages, secrets),
            "temperature": profile["temperature"],
            "max_tokens": profile["max_tokens"],
            "stream": False,
        }
        if tools:
            body["tools"] = _redact(tools, secrets)
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        try:
            async with asyncio.timeout(profile["timeout_seconds"]):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(
                        profile["timeout_seconds"], connect=min(10, profile["timeout_seconds"])
                    ),
                ) as client:
                    async with client.stream(
                        "POST",
                        profile["base_url"] + "/chat/completions",
                        json=body,
                        headers=headers,
                    ) as response:
                        if 300 <= response.status_code < 400:
                            raise ModelGatewayError(
                                "model_redirect_blocked",
                                "模型地址返回重定向，请管理员直接配置最终地址。",
                            )
                        if response.status_code in {401, 403}:
                            raise ModelGatewayError(
                                "model_auth_failed",
                                "模型服务拒绝认证，请管理员检查密钥与访问权限。",
                            )
                        if response.status_code == 429:
                            raise ModelGatewayError(
                                "model_rate_limited", "模型服务请求过多或额度不足，请稍后重试。"
                            )
                        if response.status_code >= 400:
                            raise ModelGatewayError(
                                "model_provider_error",
                                "模型服务未能完成请求，请检查模型名称、参数或稍后重试。",
                            )
                        chunks = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self._MAX_RESPONSE:
                                raise ModelGatewayError(
                                    "model_response_too_large",
                                    "模型返回内容超过允许大小，请减少输出长度。",
                                )
                            chunks.append(chunk)
                        raw = b"".join(chunks)
        except (httpx.TimeoutException, TimeoutError):
            raise ModelGatewayError(
                "model_timeout", "模型请求超时，请稍后重试或调整模型超时设置。"
            ) from None
        except httpx.RequestError:
            raise ModelGatewayError(
                "model_connection_failed", "无法连接模型服务，请检查地址、服务状态与网络。"
            ) from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ModelGatewayError(
                "model_invalid_json", "模型服务返回了无效 JSON，请检查是否为兼容的对话接口。"
            ) from None
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message.get("content")
            calls = message.get("tool_calls") or []
            if content is None:
                content = ""
            if not isinstance(content, str) or not isinstance(calls, list):
                raise ValueError()
            normalized_calls = []
            for call in calls:
                function = call["function"]
                if (
                    call.get("type") != "function"
                    or not isinstance(call.get("id"), str)
                    or not isinstance(function.get("name"), str)
                    or not isinstance(function.get("arguments"), str)
                ):
                    raise ValueError()
                normalized_calls.append(
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": function["name"], "arguments": function["arguments"]},
                    }
                )
            result = {
                "content": content,
                "tool_calls": normalized_calls,
                "model": payload.get("model") or profile["model"],
            }
            if not isinstance(result["model"], str):
                raise ValueError()
            finish_reason = choice.get("finish_reason")
            # Provider-specific diagnostics are untrusted text. Keep only known
            # enums, omitting even a known value if it matches a configured key.
            if (
                isinstance(finish_reason, str)
                and finish_reason in self._FINISH_REASONS
                and _redact(finish_reason, secrets) == finish_reason
            ):
                result["finish_reason"] = finish_reason
            if isinstance(payload.get("usage"), dict):
                result["usage"] = {
                    name: value
                    for name, value in payload["usage"].items()
                    if name in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                }
            return _redact(result, secrets)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            raise ModelGatewayError(
                "model_invalid_response", "模型返回结构不兼容，缺少有效的对话结果。"
            ) from None

    async def test_connection(self, profile_id: str) -> dict[str, Any]:
        started = time.monotonic()
        result = await self.chat(
            [
                {
                    "role": "user",
                    "content": "This is a synthetic connection test. Reply with CONNECTION_OK only.",
                }
            ],
            profile_id=profile_id,
        )
        return {
            "ok": True,
            "message": "模型连接成功，已收到真实响应。",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "model": result["model"],
            "response_preview": result["content"][:240],
        }
