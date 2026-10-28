"""Explicit AI routes for admitted modules; provider secrets stay with their owner.

Configuration controls use the administered service credential. Business requests
add the current short-lived access ticket and scoped permission. Neither channel
can substitute for the other. Bindings are atomic, single-process local state,
like existing model profiles; distributed writers need a transactional store.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import PlatformError, require
from .identity import can_manage_platform, refresh_actor
from .model_gateway import ModelGatewayError
from .registration_contract import digest
from .remote_services import RemoteServiceError


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts/ai/v1"


def contract(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def valid(schema, value, message="AI 服务输入不符合契约。"):
    require(Draft202012Validator(schema).is_valid(value), "ai_contract", message, 422)


def model_error(error):
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
    return PlatformError(error.code, error.message, status)


class AIServices:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.data_dir / "ai-service-bindings.json"
        self._lock = threading.RLock()

    def current_admin(self, actor):
        with self.runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
        require(can_manage_platform(current), "forbidden", "AI 服务配置需要平台管理权限。", 403)
        return current

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "items": []}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if set(state) != {"version", "items"} or state["version"] != 1:
                raise ValueError()
            valid(contract("binding-store.schema.json"), state)
            keys = [(item["module_id"], item["service_id"]) for item in state["items"]]
            if len(keys) != len(set(keys)):
                raise ValueError()
            return state
        except (OSError, ValueError, TypeError, KeyError, PlatformError):
            raise PlatformError("ai_config_corrupt", "AI 服务来源配置无法读取。", 503) from None

    def _write(self, state):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".ai-bindings-", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            raise PlatformError("ai_config_write_failed", "AI 服务来源配置保存失败。", 503) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _declarations(self):
        manager = self.runtime.remote_services
        if not manager:
            return []
        result = []
        for remote in manager.list_services()["items"]:
            registration = remote.get("registration") or {}
            if not registration.get("ai_services"):
                continue
            try:
                manager.require_admitted(remote)
            except RemoteServiceError:
                continue
            for service in registration["ai_services"]:
                result.append({
                    **copy.deepcopy(service),
                    "module_id": registration["module_id"],
                    "remote_service_id": remote["id"],
                    "remote_revision": remote["revision"],
                    "remote_enabled": remote["enabled"],
                    "contract_digest": digest(registration),
                })
        return result

    def declaration(self, module_id, service_id, *, active=True):
        matches = [item for item in self._declarations()
                   if item["module_id"] == module_id and item["id"] == service_id]
        require(len(matches) == 1, "ai_service_missing", "AI 服务未登记或接入声明已失效。", 404)
        declaration = matches[0]
        require(not active or declaration["remote_enabled"], "ai_module_disabled", "模块已停用。", 409)
        return declaration

    @staticmethod
    def _context(declaration):
        return {key: declaration[key] for key in (
            "remote_service_id", "remote_revision", "contract_digest"
        )}

    def _binding(self, declaration):
        state = self._read()
        return next((copy.deepcopy(item) for item in state["items"]
                     if (item["module_id"], item["service_id"]) ==
                     (declaration["module_id"], declaration["id"])), {
                         "module_id": declaration["module_id"],
                         "service_id": declaration["id"],
                         "source": "disabled", "platform_profile_id": None,
                         "revision": 0, **self._context(declaration),
                     })

    def _public(self, declaration, binding):
        return {
            **{key: copy.deepcopy(declaration[key]) for key in (
                "id", "title", "description", "capability_ids", "supported_sources",
                "module_id", "remote_service_id", "remote_revision",
            )},
            **{key: binding[key] for key in ("source", "platform_profile_id", "revision")},
            "binding_stale": any(binding[key] != declaration[key] for key in self._context(declaration)),
            "remote_enabled": declaration["remote_enabled"],
        }

    def list_services(self, actor):
        self.current_admin(actor)
        with self._lock:
            return {"items": [self._public(item, self._binding(item))
                              for item in self._declarations()]}

    def _selected(self, declaration):
        with self._lock:
            binding = self._binding(declaration)
        require(all(binding[key] == declaration[key] for key in self._context(declaration)),
                "ai_binding_stale", "模块连接或声明已变化，请重新保存 AI 来源。", 409)
        require(binding["source"] != "disabled", "ai_disabled", "该 AI 服务尚未启用。", 409)
        require(binding["source"] in declaration["supported_sources"],
                "ai_source_unsupported", "模块不支持当前 AI 来源。", 409)
        return binding

    def _profile_stamp(self, profile_id):
        try:
            profile, _, _ = self.runtime.gateway._snapshot(profile_id)
            return digest(profile)
        except ModelGatewayError as error:
            raise model_error(error) from None

    async def _control(self, declaration, method, action, body=None):
        # The path is fixed by protocol, never read from module/user metadata.
        manager = self.runtime.remote_services
        item, token, _ = manager._snapshot(declaration["remote_service_id"])
        manager._same_revision(item, declaration["remote_revision"])
        require(item["enabled"], "ai_module_disabled", "模块已停用。", 409)
        manager.require_admitted(item)
        response = await manager._request(
            item, token, method, f"ai/services/{declaration['id']}/{action}",
            body=body, limit=32768,
        )
        current = self.declaration(declaration["module_id"], declaration["id"])
        require(self._context(current) == self._context(declaration),
                "ai_connection_changed", "模块连接已变化，未采纳返回结果。", 409)
        encoded = json.dumps(response, ensure_ascii=False)
        secrets = [token]
        if body and isinstance(body.get("profile"), dict):
            secrets.append(body["profile"].get("api_key"))
        require(not any(secret and secret in encoded for secret in secrets),
                "ai_credential_echo", "模块配置响应包含凭据，未采纳。", 502)
        return response

    async def module_config(self, actor, module_id, service_id, payload=None):
        self.current_admin(actor)
        declaration = self.declaration(module_id, service_id)
        require("module" in declaration["supported_sources"],
                "ai_source_unsupported", "模块未声明自有 API 配置。", 422)
        if payload is not None:
            valid(contract("module-config-request.schema.json"), payload)
        result = await self._control(
            declaration, "PUT" if payload is not None else "GET", "config", payload
        )
        self.current_admin(actor)
        valid(contract("module-config-response.schema.json"), result, "模块配置返回结构无效。")
        return result

    async def save_binding(self, actor, module_id, service_id, payload):
        self.current_admin(actor)
        valid(contract("binding-request.schema.json"), payload)
        declaration = self.declaration(module_id, service_id)
        source = payload["source"]
        require(source == "disabled" or source in declaration["supported_sources"],
                "ai_source_unsupported", "模块未声明支持该 AI 来源。", 422)
        if source == "platform":
            self._profile_stamp(payload["platform_profile_id"])
        elif source == "module":
            profile = await self.module_config(actor, module_id, service_id)
            require(profile["profile"] is not None and profile["profile"]["enabled"],
                    "ai_module_not_configured", "请先保存并启用模块 API 配置。", 409)
        self.current_admin(actor)
        current = self.declaration(module_id, service_id)
        require(self._context(current) == self._context(declaration),
                "ai_connection_changed", "模块连接已变化，请刷新后再保存。", 409)
        with self._lock:
            previous = self._binding(declaration)
            require(previous["revision"] == payload["expected_revision"],
                    "ai_revision_conflict", "AI 来源已变化，请刷新后再保存。", 409)
            binding = {
                "module_id": module_id, "service_id": service_id,
                "source": source, "platform_profile_id": payload["platform_profile_id"],
                "revision": previous["revision"] + 1, **self._context(declaration),
            }
            state = self._read()
            state["items"] = [item for item in state["items"] if
                              (item["module_id"], item["service_id"]) != (module_id, service_id)]
            state["items"].append(binding)
            self._write(state)
            return self._public(declaration, binding)

    def consumer(self, ticket, body):
        decision = self.runtime.access_bridge.decide(
            ticket, body["request_id"], body["permission"], body["facts"],
            operation=body["operation"],
        )
        require(decision["allowed"], decision["reason"], "当前 AI 业务授权无效。", 403)
        claims = self.runtime.access_bridge._verify(ticket)
        declaration = self.declaration(claims["binding"]["module_id"], body["service_id"])
        require(claims["binding"]["id"] in declaration["capability_ids"]
                and claims["binding"]["service_id"] == declaration["remote_service_id"]
                and claims["binding"]["binding_revision"] == declaration["remote_revision"],
                "ai_capability_mismatch", "AI 服务不属于本次已授权能力。", 403)
        return declaration, self._selected(declaration)

    @staticmethod
    def route(declaration, binding, config_revision=None, platform_digest=None):
        return {"service_id": declaration["id"], "source": binding["source"],
                "binding_revision": binding["revision"], "module_config_revision": config_revision,
                "platform_config_digest": platform_digest}

    async def resolve(self, ticket, body):
        valid(contract("resolve-request.schema.json"), body)
        declaration, binding = self.consumer(ticket, body)
        config_revision = None
        platform_digest = None
        if binding["source"] == "module":
            config = await self._control(declaration, "GET", "config")
            valid(contract("module-config-response.schema.json"), config)
            require(config["profile"] is not None and config["profile"]["enabled"],
                    "ai_module_not_configured", "模块 API 尚未配置或已禁用。", 409)
            config_revision = config["revision"]
        else:
            platform_digest = self._profile_stamp(binding["platform_profile_id"])
        _, current = self.consumer(ticket, body)
        require(current == binding, "ai_revision_conflict", "AI 来源已变化，未采纳结果。", 409)
        return self.route(declaration, binding, config_revision, platform_digest)

    async def validate(self, ticket, body):
        valid(contract("validate-request.schema.json"), body)
        declaration, binding = self.consumer(ticket, body)
        require(body["binding_revision"] == binding["revision"],
                "ai_revision_conflict", "AI 来源已变化，未采纳结果。", 409)
        if binding["source"] == "module":
            require(body["platform_config_digest"] is None,
                    "ai_contract", "模块来源不能携带平台配置摘要。", 422)
            config = await self._control(declaration, "GET", "config")
            valid(contract("module-config-response.schema.json"), config)
            require(config["profile"] is not None and config["profile"]["enabled"]
                    and config["revision"] == body.get("module_config_revision"),
                    "ai_module_config_changed", "模块 API 配置已变化，未采纳结果。", 409)
        else:
            require(body.get("module_config_revision") is None,
                    "ai_contract", "平台来源不能携带模块配置修订。", 422)
            require(self._profile_stamp(binding["platform_profile_id"]) == body["platform_config_digest"],
                    "ai_platform_config_changed", "平台模型配置已变化，未采纳结果。", 409)
        _, current = self.consumer(ticket, body)
        require(current == binding, "ai_revision_conflict", "AI 来源已变化，未采纳结果。", 409)
        return self.route(declaration, binding, body["module_config_revision"], body["platform_config_digest"])

    async def chat(self, ticket, body):
        valid(contract("chat-request.schema.json"), body)
        declaration, binding = self.consumer(ticket, body)
        require(binding["source"] == "platform", "ai_source_mismatch", "当前 AI 来源不是平台。", 409)
        require(binding["revision"] == body["binding_revision"],
                "ai_revision_conflict", "AI 来源已变化，请重新解析来源。", 409)
        require(body.get("module_config_revision") is None,
                "ai_contract", "平台来源不能携带模块配置修订。", 422)
        stamp = self._profile_stamp(binding["platform_profile_id"])
        require(stamp == body["platform_config_digest"],
                "ai_platform_config_changed", "平台模型配置已变化，请重新解析来源。", 409)
        try:
            result = await self.runtime.gateway.chat(body["messages"], binding["platform_profile_id"])
        except ModelGatewayError as error:
            raise model_error(error) from None
        validation = {key: value for key, value in body.items() if key != "messages"}
        await self.validate(ticket, validation)
        require(self._profile_stamp(binding["platform_profile_id"]) == stamp,
                "ai_platform_config_changed", "平台模型配置已变化，未采纳结果。", 409)
        require(ticket not in json.dumps(result, ensure_ascii=False),
                "ai_credential_echo", "AI 返回内容包含调用凭据，未采纳。", 502)
        return {**self.route(declaration, binding, platform_digest=stamp), "result": result}

    async def test(self, actor, module_id, service_id, payload):
        self.current_admin(actor)
        valid(contract("test-request.schema.json"), payload)
        declaration = self.declaration(module_id, service_id)
        binding = self._selected(declaration)
        require(binding["revision"] == payload["expected_revision"],
                "ai_revision_conflict", "AI 来源已变化，请刷新后测试。", 409)
        config_revision = None
        platform_digest = None
        if binding["source"] == "platform":
            stamp = self._profile_stamp(binding["platform_profile_id"])
            platform_digest = stamp
            try:
                result = await self.runtime.gateway.test_connection(binding["platform_profile_id"])
            except ModelGatewayError as error:
                raise model_error(error) from None
            require(self._profile_stamp(binding["platform_profile_id"]) == stamp,
                    "ai_platform_config_changed", "平台模型配置已变化，未采纳测试结果。", 409)
        else:
            config = await self._control(declaration, "GET", "config")
            valid(contract("module-config-response.schema.json"), config)
            config_revision = config["revision"]
            result = await self._control(declaration, "POST", "test", {"expected_revision": config["revision"]})
            after = await self._control(declaration, "GET", "config")
            require(after == config, "ai_module_config_changed", "模块配置已变化，未采纳测试结果。", 409)
        self.current_admin(actor)
        current = self.declaration(module_id, service_id)
        require(self._context(current) == self._context(declaration)
                and self._selected(current) == binding,
                "ai_revision_conflict", "AI 来源已变化，未采纳测试结果。", 409)
        valid(contract("test-result.schema.json"), result, "AI 测试响应不符合契约。")
        return {**self.route(declaration, binding, config_revision, platform_digest), "result": result}
