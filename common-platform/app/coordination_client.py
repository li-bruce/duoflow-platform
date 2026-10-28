"""Control-only bridge to the independent business coordination center."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import secrets

import httpx
from pydantic import ValidationError

from coordination_service.protocol import Config, Module, SummaryApproval, validate_summary, validate_configuration
from .errors import PlatformError, require
from .model_gateway import ModelGateway
from monitoring_service.settings import atomic_json, seal, unseal


class CoordinationClient:
    def __init__(self, directory, manager, *, transport=None):
        self.directory, self.manager = Path(directory), manager
        self.transport = transport
        self.lock = asyncio.Lock()

    def connection(self):
        value = json.loads((self.directory / "coordination-connection.json").read_text(encoding="utf-8"))
        require(value["version"] == "1.0" and ModelGateway._base_url(value["url"]) == value["url"],
                "coordination_config", "业务协同中心连接配置无效。", 503)
        return value, unseal(self.directory, value["secret"])

    def modules(self):
        result = []
        for item in self.manager.list_services()["items"]:
            declaration = (item.get("registration") or {}).get("coordination")
            if not declaration or not item.get("enabled") or item.get("discovered_revision") != item["revision"]:
                continue
            admission = item.get("admission") or {}
            if not admission.get("passed") or admission.get("revision") != item["revision"]:
                continue
            identifier = item["manifest"]["module"]["id"]
            try:
                normalized = Module.model_validate({"id": identifier, "token": "validation-placeholder", "enabled": True,
                                                   **{key: declaration[key] for key in ("machines", "emits", "accepts")}}).model_dump(exclude={"token", "enabled", "id"})
            except (ValidationError, ValueError, KeyError, TypeError):
                raise PlatformError("coordination_declaration", "模块的协同声明需要重新验收。", 409) from None
            result.append({"id": identifier, "title": item["manifest"]["module"]["name"], "service_id": item["id"],
                           "service_revision": item["revision"], "declaration": {"version": "1.0", **normalized}})
        require(len({x["id"] for x in result}) == len(result), "coordination_module_duplicate", "模块标识重复。", 409)
        return result

    def _settings(self):
        path = self.directory / "coordination-settings.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"module_ids": [], "subscriptions": [], "bindings": {}}

    def settings(self):
        value = self._settings()
        pending_path = self.directory / "coordination-pending.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else None
        return {"module_ids": value["module_ids"], "subscriptions": value["subscriptions"],
                "status": "pending" if pending else value.get("status", "unconfigured"),
                "pending_change": {k: pending[k] for k in ("module_ids", "subscriptions", "config_revision")} if pending else None}

    @staticmethod
    def _response(method, path, value, payload):
        if method == "GET" and path == "/summary":
            return validate_summary(value)
        if method == "GET" and path == "/config":
            return validate_configuration(value)
        require(isinstance(value, dict) and value.get("version") == "1.0", "coordination_response", "协同响应格式不匹配。", 502)
        if method == "PUT" and path == "/config":
            require(set(value) == {"version", "config_revision"} and type(value["config_revision"]) is int
                    and value["config_revision"] == payload["expected_revision"] + 1,
                    "coordination_response", "协同配置回执不匹配。", 502)
            return {"version": "1.0", "config_revision": value["config_revision"]}
        if method == "POST" and re.fullmatch(r"/approvals/[A-Za-z0-9_.:-]{1,160}/decide", path):
            require(set(value) == {"version", "approval", "delivery_id"}
                    and isinstance(value["delivery_id"], str) and re.fullmatch(r"[a-f0-9]{32}", value["delivery_id"]) is not None,
                    "coordination_response", "协同审批回执不匹配。", 502)
            approval = SummaryApproval.model_validate(value["approval"]).model_dump()
            require(approval["event_id"] == path.split("/")[2] and approval["revision"] == payload["expected_revision"] + 1
                    and approval["status"] == payload["decision"] and approval["actor_id"] == payload["actor_id"],
                    "coordination_response", "协同审批回执不匹配。", 502)
            return {"version": "1.0", "approval": approval, "delivery_id": value["delivery_id"]}
        raise PlatformError("coordination_request", "协同控制接口无效。", 422)

    async def request(self, method, path, payload=None):
        require((method == "GET" and path in {"/summary", "/config"}) or (method == "PUT" and path == "/config")
                or (method == "POST" and re.fullmatch(r"/approvals/[A-Za-z0-9_.:-]{1,160}/decide", path) is not None),
                "coordination_request", "协同控制接口无效。", 422)
        sent = False
        try:
            config, token = self.connection()
            async with asyncio.timeout(4):
                async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False, timeout=4) as client:
                    sent = True
                    async with client.stream(method, config["url"] + "/api/v1/coordination" + path,
                                             headers={"Authorization": "Bearer " + token}, json=payload) as response:
                        unknown = method != "GET" and response.status_code >= 500
                        require(response.status_code == 200, "coordination_conflict" if response.status_code == 409 else "coordination_outcome_unknown" if unknown else "coordination_request",
                                "协同配置或审批版本已变化，请刷新。" if response.status_code == 409 else "业务协同中心未完成该操作。",
                                409 if response.status_code == 409 else 503 if unknown else 502)
                        raw = bytearray()
                        maximum = 524288 if path == "/config" else 262144
                        async for chunk in response.aiter_bytes(4096):
                            require(len(raw) + len(chunk) <= maximum, "coordination_size", "协同汇总内容过大。", 502)
                            raw.extend(chunk)
                        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        return self._response(method, path, value, payload)
        except PlatformError as error:
            if sent and method != "GET" and error.code not in {"coordination_conflict", "coordination_request"}:
                raise PlatformError("coordination_outcome_unknown", "操作已发送，回执尚未确认，请刷新协同中心后核对。", 503) from None
            raise
        except Exception:
            code = "coordination_outcome_unknown" if sent and method != "GET" else "coordination_unavailable"
            message = "操作已发送，回执尚未确认，请刷新协同中心后核对。" if code.endswith("unknown") else "业务协同中心暂未连接，请检查独立服务。"
            raise PlatformError(code, message, 503) from None

    async def reconcile(self):
        """Revoke changed/withdrawn module bindings; only an admin configure can enable."""
        if not (self.directory / "coordination-connection.json").exists():
            return None
        async with self.lock:
            saved = self._settings()
            pending_path = self.directory / "coordination-pending.json"
            pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else None
            if not saved.get("bindings") and not pending:
                return None
            available = {v["id"]: v for v in self.modules()}
            actual = await self.request("GET", "/config")
            known = deepcopy(saved.get("bindings", {}))
            if pending:
                pending_modules = [{"id": key, "enabled": binding.get("enabled", True),
                                    **{k: binding["declaration"][k] for k in ("machines", "emits", "accepts")}}
                                   for key, binding in pending["bindings"].items()]
                same_modules = sorted(actual["modules"], key=lambda m: m["id"]) == sorted(pending_modules, key=lambda m: m["id"])
                same_subscriptions = sorted(actual["subscriptions"], key=lambda s: s["id"]) == sorted(pending["subscriptions"], key=lambda s: s["id"])
                if same_modules and same_subscriptions and actual["config_revision"] >= pending["config_revision"] + 1:
                    saved = {**pending, "config_revision": actual["config_revision"], "status": "confirmed"}
                    atomic_json(self.directory / "coordination-settings.json", saved)
                    known = deepcopy(saved["bindings"])
                    pending_path.unlink(missing_ok=True)
                else:
                    # Pending identities can revoke an uncertain write, but
                    # reconciliation never enables or installs that intent.
                    known.update(pending["bindings"])
            modules, bindings, changed = [], {}, False
            secrets_path = self.directory / "coordination-module-secrets.json"
            credentials = json.loads(secrets_path.read_text(encoding="utf-8")) if secrets_path.exists() else {}
            for configured in actual["modules"]:
                key = configured["id"]
                binding, current = known.get(key), available.get(key)
                declaration = {"version": "1.0", **{k: configured[k] for k in ("machines", "emits", "accepts")}}
                matches = binding is not None and current is not None and all(current[k] == binding[k] for k in ("service_id", "service_revision", "declaration")) and declaration == binding["declaration"]
                enabled = configured["enabled"] and matches and binding.get("enabled", True)
                changed = changed or enabled != configured["enabled"]
                if key not in credentials:
                    credentials[key] = seal(self.directory, secrets.token_urlsafe(48))
                modules.append({**configured, "enabled": bool(enabled), "token": unseal(self.directory, credentials[key])})
                bindings[key] = {**(binding or {"id": key, "service_id": "", "service_revision": 0}), "declaration": declaration, "enabled": bool(enabled)}
            if changed:
                atomic_json(secrets_path, credentials)
                result = await self.request("PUT", "/config", {"expected_revision": actual["config_revision"], "modules": modules,
                                                              "subscriptions": actual["subscriptions"]})
                atomic_json(self.directory / "coordination-settings.json", {"module_ids": [m["id"] for m in modules],
                            "subscriptions": actual["subscriptions"], "bindings": bindings,
                            "config_revision": result["config_revision"], "status": "confirmed"})
            return await self.request("GET", "/summary")

    async def summary(self):
        if not (self.directory / "coordination-connection.json").exists():
            return {"state": "unconfigured", "summary": None}
        try:
            value = await self.reconcile()
            return {"state": "connected", "summary": value if value is not None else await self.request("GET", "/summary")}
        except PlatformError:
            return {"state": "unavailable", "summary": None}

    async def configure(self, payload, before_send):
        require(isinstance(payload, dict) and set(payload) == {"expected_revision", "module_ids", "subscriptions"},
                "coordination_config", "请选择模块并填写订阅关系。", 422)
        require(type(payload["expected_revision"]) is int and payload["expected_revision"] >= 0,
                "coordination_revision", "配置修订无效。", 422)
        ids = payload["module_ids"]
        require(isinstance(ids, list) and len(ids) <= 100 and all(isinstance(x, str) for x in ids) and len(set(ids)) == len(ids),
                "coordination_modules", "模块选择无效。", 422)
        async with self.lock:
            available = {x["id"]: x for x in self.modules()}
            require(set(ids) <= available.keys(), "coordination_modules", "请先接入并发布模块的协同声明。", 422)
            path = self.directory / "coordination-module-secrets.json"
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            modules, bindings = [], {}
            for key in ids:
                if key not in saved:
                    saved[key] = seal(self.directory, secrets.token_urlsafe(48))
                declaration = available[key]["declaration"]
                modules.append({"id": key, "token": unseal(self.directory, saved[key]), "enabled": True,
                                **{k: declaration[k] for k in ("machines", "emits", "accepts")}})
                bindings[key] = {**available[key], "enabled": True}
            try:
                command = Config.model_validate({"expected_revision": payload["expected_revision"], "modules": modules,
                                                 "subscriptions": payload["subscriptions"]}).model_dump()
            except (ValueError, TypeError):
                raise PlatformError("coordination_config", "订阅需匹配两侧声明及明确的项目和机构。", 422) from None
            before_send()
            atomic_json(path, saved)
            settings = {"module_ids": ids, "subscriptions": command["subscriptions"], "bindings": bindings,
                        "config_revision": payload["expected_revision"], "status": "pending"}
            pending_path = self.directory / "coordination-pending.json"
            atomic_json(pending_path, settings)
            try:
                result = await self.request("PUT", "/config", command)
            except PlatformError as error:
                if error.code != "coordination_outcome_unknown":
                    pending_path.unlink(missing_ok=True)
                raise
            atomic_json(self.directory / "coordination-settings.json", {**settings, "config_revision": result["config_revision"], "status": "confirmed"})
            pending_path.unlink(missing_ok=True)
            before_send()
            return result
