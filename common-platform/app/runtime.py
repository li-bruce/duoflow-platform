"""Platform capability execution and publication, independent of business rules.

Identity, scope checks, request receipts and private host state are composed here.
Business modules retain their own records and state transitions. Registration
validates interfaces and installed dependencies; it never grants business access.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import ipaddress
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .contracts import CapabilityContext, CapabilityOutput
from .db import (
    Audit,
    Conversation,
    Invocation,
    Message,
    Resource,
    ResourceVersion,
    Run,
    User,
    audit,
    now,
    resource_dict,
    run_dict,
    uid,
    user_dict,
)
from .errors import PlatformError, require
from .registry import Registry
from .module_contract import normalize_surfaces
from .module_state import ModuleState
from .identity import (
    effective_roles,
    has_role,
    authorization_snapshot,
    refresh_actor,
    can_manage_platform,
    capability_allowed,
)
from .workspace_contract import validate_workspace, validate_files, MAX_INVOKE_BYTES
from .access_control import AccessControl


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def web_origin(value):
    """A canonical origin safe to use as one CSP source, without URL paths."""
    parsed = urlsplit(value)
    try:
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(host)
            host = "[" + address.compressed + "]" if address.version == 6 else address.compressed
        except ValueError:
            require(
                bool(re.fullmatch(r"[a-z0-9.-]+", host)),
                "module_page_origin",
                "网页工具的服务来源无效。",
                422,
            )
        require(
            parsed.scheme in ("http", "https")
            and parsed.username is None
            and parsed.password is None,
            "module_page_origin",
            "网页工具的服务来源无效。",
            422,
        )
        port = parsed.port
        suffix = (
            ""
            if port is None or port == (443 if parsed.scheme == "https" else 80)
            else ":" + str(port)
        )
        return parsed.scheme.lower() + "://" + host + suffix
    except (ValueError, UnicodeError):
        raise PlatformError("module_page_origin", "网页工具的服务来源无效。", 422) from None


def accessible(resource, actor):
    require(resource is not None, "not_found", "没有找到这个结果。", 404)
    if resource.access_stamp:
        from sqlalchemy.orm import object_session

        require(
            resource.owner_id == actor["id"]
            and resource.access_stamp
            == AccessControl.from_session(object_session(resource)).authorization_stamp(actor),
            "authorization_changed",
            "此业务结果的授权已变化，或不属于当前用户。",
            403,
        )
    require(
        resource.owner_id == actor["id"] or actor["role"] == "admin",
        "forbidden",
        "无权访问其他使用者的结果。",
        403,
    )
    require(
        actor["role"] == "admin"
        or getattr(resource, "authorization_revision", 0)
        in (0, actor.get("authorization_revision", 1)),
        "authorization_changed",
        "结果对应的授权范围已变化，请重新办理或联系业务管理员。",
        403,
    )


class Runtime:
    def __init__(self, root: Path, sessions, gateway, remote_services=None, data_dir=None):
        self.root, self.sessions, self.gateway = root, sessions, gateway
        self.data_dir = Path(data_dir) if data_dir is not None else Path(root) / "data"
        self.remote_services = remote_services
        self.module_state = ModuleState(data_dir)
        self.access = AccessControl(sessions)
        self._load_access_definitions()
        self.registry = Registry(root)
        self.registry.load()
        self.flow = json.loads((root / "config/review-flow.json").read_text(encoding="utf-8-sig"))
        self.lock = asyncio.Lock()
        self.worker_task = None
        self.current_job = None
        self.current_run = None
        self._register_core()
        self.local_registry = self.registry
        self.registry = self.build_remote_registry(
            self.remote_services.enabled_bindings() if self.remote_services else []
        )
        from .interactions import Interactions

        self.interactions = Interactions(self)
        from .workflows import Workflows

        self.workflows = Workflows(self)
        from .access_bridge import AccessBridge

        self.access_bridge = AccessBridge(self)
        # Host packages/policy can change while this process is stopped. A saved
        # receipt cannot substitute for checking the currently installed pieces.
        if self.remote_services:
            for binding in self.remote_services.enabled_bindings():
                service = next(
                    item
                    for item in self.remote_services.list_services()["items"]
                    if item["id"] == binding["service_id"]
                )
                try:
                    self.check_remote_registration(binding["service_id"], service["registration"])
                except PlatformError:
                    self.remote_services.invalidate_host_admission(
                        binding["service_id"], binding["revision"]
                    )
            self.registry = self.build_remote_registry(self.remote_services.enabled_bindings())

    def _load_access_definitions(self):
        """Only locally administered installations may publish policy metadata."""
        installed = self.root / "extensions/installed.json"
        if not installed.exists():
            return
        for folder in json.loads(installed.read_text(encoding="utf-8-sig"))["modules"]:
            from .registry import inside

            path = inside(self.root / "extensions", f"{folder}/access-definition.json")
            if path.exists():
                definition = json.loads(path.read_text(encoding="utf-8-sig"))
                manifest = json.loads(
                    (path.parent / "manifest.json").read_text(encoding="utf-8-sig")
                )
                require(
                    definition["module_id"] == manifest["id"],
                    "access_module",
                    "权限定义必须属于其安装模块。",
                    422,
                )
                ids = {cap["id"] for cap in manifest["capabilities"]}
                require(
                    all(
                        mapping["capability_id"] in ids
                        for mapping in definition["command_mappings"]
                    ),
                    "access_mapping",
                    "权限映射引用了模块之外的能力。",
                    422,
                )
                self.access.register_definition(definition, actor_id="system:installation")

    def access_allowed(self, actor, spec):
        if not self.access.module_registered(spec["module_id"]):
            return None
        permissions = self.access.capability_permissions(spec["id"])
        return bool(permissions) and any(self.access.has_permission(actor, p) for p in permissions)

    def access_snapshot(self, actor, spec):
        snapshot = copy.deepcopy(actor)
        if self.access.module_registered(spec["module_id"]):
            snapshot["_access_stamp"] = self.access.authorization_stamp(actor)
        return snapshot

    @staticmethod
    def execution_revision(spec):
        # Pin both the provider connection and its discovered contract without
        # changing the public semantic capability version or existing DB schema.
        if spec.get("service_id"):
            return fingerprint(
                {
                    "service": spec["service_id"],
                    "revision": spec["binding_revision"],
                    "version": spec["version"],
                    "activation": spec.get("module_revision", 1),
                }
            )[:30]
        if spec.get("module_revision", 1) > 1:
            return fingerprint({"version": spec["version"], "activation": spec["module_revision"]})[
                :30
            ]
        return spec["version"]

    def redact(self, text):
        text = self.gateway.redact(text)
        return self.remote_services.redact(text) if self.remote_services else text

    def build_remote_registry(self, bindings, overrides=None):
        overrides = overrides or {}
        candidate = Registry(self.root)
        candidate.availability = self.availability
        candidate.access_allowed = self.access_allowed
        for module in self.local_registry.modules:
            state = overrides.get(module["id"], self.module_state.get(module["id"]))
            if module["id"] != "core" and not state["enabled"]:
                continue
            item = {**module, "revision": state["revision"], "enabled": True}
            candidate.modules.append(item)
            for cap_id, spec in self.local_registry.capabilities.items():
                if spec["module_id"] == module["id"]:
                    candidate.register(
                        item,
                        {**spec, "module_revision": state["revision"]},
                        self.local_registry.handlers[cap_id],
                    )
        for binding in bindings:
            manifest = binding["manifest"]
            module = {
                **manifest["module"],
                "source": "remote",
                "service_id": binding["service_id"],
                "revision": binding["revision"],
                "enabled": True,
                "surfaces": normalize_surfaces(
                    manifest["module"]["id"],
                    manifest["capabilities"],
                    manifest.get("surfaces"),
                    allow_embedded=True,
                    allow_skills=manifest["protocol_version"] == "1.2",
                ),
            }
            require(
                not any(
                    m["id"] == module["id"]
                    for m in [*candidate.modules, *self.local_registry.modules]
                ),
                "manifest_duplicate",
                "远程模块标识与已安装模块重复，未启用。",
                409,
            )
            candidate.modules.append(module)
            for raw in manifest["capabilities"]:
                require(
                    raw["id"] not in self.local_registry.capabilities,
                    "manifest_duplicate",
                    "远程能力标识与已安装能力重复，未启用。",
                    409,
                )
                spec = {
                    **raw,
                    "service_id": binding["service_id"],
                    "binding_revision": binding["revision"],
                    "remote_protocol": manifest["protocol_version"],
                    "module_revision": overrides.get(
                        module["id"], self.module_state.get(module["id"])
                    )["revision"],
                }
                candidate.register(module, spec, self.remote_handler(binding, spec))
        return candidate

    def availability(self, spec):
        if not spec.get("service_id"):
            return {"state": "local", "available": True, "message": "本地模块可用。"}
        status = self.remote_services.monitor_status(spec["service_id"])
        return {
            "state": status["state"],
            "available": status["state"] not in ("offline", "stale"),
            "message": status["message"],
        }

    def public_registry(self, actor):
        public = self.registry.public(actor)
        for module in public["modules"]:
            if module.get("service_id"):
                module["monitor"] = self.remote_services.monitor_status(module["service_id"])
        return public

    def conversation_capabilities(self, actor):
        public = self.public_registry(actor)
        ids = {
            cap_id
            for m in public["modules"]
            for cap_id in m["surfaces"]["conversation"]["capability_ids"]
        }
        return [
            s for s in public["capabilities"] if s["id"] in ids and s["availability"]["available"]
        ]

    def check_available(self, spec):
        if spec.get("service_id"):
            from .remote_services import RemoteServiceError

            try:
                self.remote_services.check_available(spec["service_id"])
            except RemoteServiceError as exc:
                raise PlatformError(exc.code, exc.message, 503) from None

    def modules(self):
        items = []
        for module in self.local_registry.modules:
            state = self.module_state.get(module["id"])
            caps = [
                dict(s)
                for s in self.local_registry.capabilities.values()
                if s["module_id"] == module["id"]
            ]
            items.append(
                {
                    **module,
                    **state,
                    "protected": module["id"] == "core",
                    "capabilities": caps,
                    "surfaces": module.get("surfaces") or normalize_surfaces(module["id"], caps),
                }
            )
        if self.remote_services:
            for service in self.remote_services.list_services()["items"]:
                manifest = service.get("manifest")
                if not manifest:
                    continue
                items.append(
                    {
                        **manifest["module"],
                        "source": "remote",
                        "enabled": service["enabled"],
                        "protected": False,
                        "revision": service["revision"],
                        "service_id": service["id"],
                        "monitor": service["monitor"],
                        "capabilities": manifest["capabilities"],
                        "surfaces": normalize_surfaces(
                            manifest["module"]["id"],
                            manifest["capabilities"],
                            manifest.get("surfaces"),
                            allow_embedded=True,
                            allow_skills=manifest["protocol_version"] == "1.2",
                        ),
                    }
                )
        return {"items": items}

    def check_remote_registration(self, service_id, document):
        """Check installed, versioned host dependencies without granting access.

        Module responses cannot install UI code or policy. Administrators install
        and register these separately; acceptance only compares existing facts.
        Repeated at publication so a later policy edit cannot use stale approval.
        """
        module_id = document["module_id"]
        binding = self.remote_services.get_binding(service_id)
        require(
            binding["manifest"]["module"]["id"] == module_id,
            "registration_module",
            "接入声明与服务模块不一致。",
            422,
        )
        caps = {cap["id"]: cap for cap in binding["manifest"]["capabilities"]}
        if document["permissions"]["mode"] == "platform_access":
            installed = next(
                (
                    definition
                    for definition in self.access.definitions()
                    if definition["module_id"] == module_id
                ),
                None,
            )
            declared = document["permissions"]["definition"]
            require(
                installed is not None
                and installed["enabled"]
                and all(installed.get(key) == value for key, value in declared.items()),
                "registration_permissions",
                "平台尚未注册匹配且启用的模块权限定义。",
                409,
            )
        else:
            require(
                not self.access.module_registered(module_id),
                "registration_permissions",
                "平台已有范围权限定义，模块不能改用固定角色绕过。",
                409,
            )
        for kind, collection in (
            ("workflows", self.workflows.definitions),
            ("interactions", self.interactions.definitions),
        ):
            for reference in document["host_requirements"][kind]:
                definition = collection.get(reference["id"])
                require(
                    definition is not None and definition["version"] == reference["version"],
                    "registration_host",
                    "所需共享组件或流程尚未安装，或版本不匹配。",
                    409,
                )
                if kind == "workflows":
                    provider = definition["provider"]
                    require(
                        provider["read_capability"] in caps,
                        "registration_host",
                        "流程数据接口必须属于注册模块。",
                        409,
                    )
                    require(
                        {"objects.list", "objects.read"}.issubset(document["workflow_operations"]),
                        "registration_host",
                        "流程服务必须确认对象列表与读取接口。",
                        409,
                    )
                    require(
                        any(
                            obj["id"] == provider["object_type"]
                            and obj["operations"].get("read") == provider["read_capability"]
                            for obj in document["data_objects"]
                        ),
                        "registration_host",
                        "流程对象必须登记真实读取接口。",
                        409,
                    )
                    actions = [
                        action for step in definition["steps"] for action in step.get("actions", [])
                    ]
                else:
                    actions = definition["actions"]
                require(
                    all(
                        action["capability_id"] in caps
                        and caps[action["capability_id"]]["version"] == action["capability_version"]
                        for action in actions
                    ),
                    "registration_host",
                    "共享呈现绑定的能力或版本与模块声明不一致。",
                    409,
                )
        # Detect collisions against both installed local modules and published
        # remote modules before a report can claim successful host integration.
        bindings = [
            b for b in self.remote_services.enabled_bindings() if b["service_id"] != service_id
        ]
        self.build_remote_registry([*bindings, binding])

    def set_remote_publication(self, service_id, enabled, expected_revision):
        service = next(
            (s for s in self.remote_services.list_services()["items"] if s["id"] == service_id),
            None,
        )
        require(service is not None, "remote_service_missing", "服务连接不存在。", 404)
        require(
            service["revision"] == expected_revision,
            "remote_revision_conflict",
            "连接已经变化，请重新核对。",
            409,
        )
        binding = self.remote_services.get_binding(service_id)
        require(
            not enabled or bool(binding.get("manifest")),
            "remote_not_discovered",
            "请先发现并预览服务能力。",
            409,
        )
        if enabled:
            self.remote_services.require_admitted(service)
            self.check_remote_registration(service_id, service["registration"])
        changed = enabled != service["enabled"]
        if changed:
            # Connection revision is also the remote publication generation.
            # Build against the revision set_enabled will atomically persist;
            # no second module-state file is needed for remote activation.
            binding = {**binding, "revision": expected_revision + 1}
        bindings = [
            b for b in self.remote_services.enabled_bindings() if b["service_id"] != service_id
        ]
        if enabled:
            bindings.append(binding)
        candidate = self.build_remote_registry(bindings)
        result = self.remote_services.set_enabled(service_id, enabled, expected_revision)
        self.registry = candidate
        return result

    async def set_module_publication(self, module_id, enabled, expected_revision, *, actor=None):
        async with self.lock:
            if actor is not None:
                with self.sessions() as db:
                    actor = refresh_actor(db, actor, require_unchanged=True)
                require(can_manage_platform(actor), "forbidden", "管理模块需要平台管理权限。", 403)
            matches = [m for m in self.modules()["items"] if m["id"] == module_id]
            require(bool(matches), "module_missing", "模块尚未安装或发现。", 404)
            require(
                len(matches) == 1,
                "module_ambiguous",
                "多个服务声明同名模块，请在服务接入中核对。",
                409,
            )
            item = matches[0]
            require(not item["protected"], "module_protected", "平台核心模块保持启用。", 409)
            require(
                item["revision"] == expected_revision,
                "module_revision_conflict",
                "模块已变化，请刷新后重试。",
                409,
            )
            if item["source"] == "remote":
                self.set_remote_publication(item["service_id"], enabled, expected_revision)
            elif item["enabled"] != enabled:
                overrides = {module_id: {"enabled": enabled, "revision": expected_revision + 1}}
                candidate = self.build_remote_registry(
                    self.remote_services.enabled_bindings() if self.remote_services else [],
                    overrides,
                )
                self.module_state.change(module_id, enabled, expected_revision)
                self.registry = candidate
            return next(m for m in self.modules()["items"] if m["id"] == module_id)

    def module_page(self, module_id, tool_id, actor, platform_origin=None):
        module = next(
            (m for m in self.public_registry(actor)["modules"] if m["id"] == module_id), None
        )
        require(module is not None, "module_missing", "模块未发布或当前身份不可访问。", 404)
        page = next((p for p in module["surfaces"]["page_tools"] if p["id"] == tool_id), None)
        require(
            page is not None and page["kind"] == "embedded",
            "module_page_missing",
            "未注册可嵌入的页面工具。",
            404,
        )
        for cap_id in page["capability_ids"]:
            self.check_available(self.registry.get(cap_id, actor))
        service = next(
            (
                s
                for s in self.remote_services.list_services()["items"]
                if s["id"] == module["service_id"]
            ),
            None,
        )
        require(service is not None and service["enabled"], "module_disabled", "模块已停用。", 409)
        origin = web_origin(service["base_url"])
        require(
            platform_origin is None or origin != web_origin(platform_origin),
            "module_page_origin",
            "独立网页工具必须使用不同于平台的来源。",
            422,
        )
        return {
            "module_id": module_id,
            "tool_id": tool_id,
            "title": page["title"],
            "description": page["description"],
            "url": service["base_url"] + page["path"],
            "capability_ids": page["capability_ids"],
        }

    def frame_sources(self, actor=None):
        origins = set()
        if self.remote_services and actor:
            # CSP needs allowed frame origins only, not full schemas or health
            # status for every capability on every API response.
            visible_ids = {
                m["service_id"]
                for m in self.registry.modules
                if m.get("service_id")
                and any(
                    p["kind"] == "embedded"
                    and all(
                        cap_id in self.registry.capabilities
                        and self.registry.allowed(actor, self.registry.capabilities[cap_id])
                        for cap_id in p["capability_ids"]
                    )
                    for p in m.get("surfaces", {}).get("page_tools", [])
                )
            }
            for service in self.remote_services.list_services()["items"] if visible_ids else []:
                if service["enabled"] and service["id"] in visible_ids:
                    origins.add(web_origin(service["base_url"]))
        return sorted(origins)

    def remote_handler(self, binding, spec):
        async def handle(ctx, payload):
            from .remote_services import RemoteServiceError

            require(
                ctx.invocation_id is not None,
                "remote_request_id",
                "远程请求缺少平台调用标识。",
                500,
            )
            modern = binding["manifest"]["protocol_version"] == "1.2"
            invocation = ctx.session.get(Invocation, ctx.invocation_id)
            principal = invocation.principal_snapshot or ctx.actor
            remote_actor = {"id": principal["id"], "role": principal["role"]}
            if modern:
                remote_actor.update(
                    roles=sorted(effective_roles(principal)),
                    name=principal.get("name", principal["id"]),
                    organization_id=principal.get("organization_id"),
                    team_ids=principal.get("team_ids", []),
                    authorization_revision=principal.get("authorization_revision", 1),
                )
            request = {
                "protocol_version": "1.1" if modern else "1.0",
                "request_id": ctx.invocation_id,
                "capability_id": spec["id"],
                "capability_version": spec["version"],
                "input": payload,
                "context": {"workspace_id": ctx.workspace_id, "actor": remote_actor},
            }
            try:
                if self.access.module_registered(binding["manifest"]["module"]["id"]):
                    ticket = self.access_bridge.issue(
                        ctx.actor,
                        self.registry.get(spec["id"], ctx.actor),
                        ctx.invocation_id,
                        "invoke",
                    )
                    response = await self.remote_services.invoke(
                        binding["service_id"], binding["revision"], request, access_ticket=ticket
                    )
                else:
                    response = await self.remote_services.invoke(
                        binding["service_id"], binding["revision"], request
                    )
            except RemoteServiceError as exc:
                raise PlatformError(exc.code, exc.message, 502) from None
            # Remote services return data only. Object ownership, IDs, files and
            # UI components are all created and checked by the local platform.
            self.registry.validate(spec["output_schema"], response["data"])
            data = response["data"]
            if spec["presentation"][ctx.origin]["renderer"] == "workspace":
                data = self.refresh_workspace(
                    data, ctx.actor, spec["id"], ctx.actor.get("authorization_revision")
                )
            summary = self.redact(response["summary"])
            require(
                self.redact(json.dumps(data, ensure_ascii=False))
                == json.dumps(data, ensure_ascii=False),
                "remote_secret_output",
                "远程结果包含已配置凭据，结果未保存。",
                502,
            )
            resource, artifacts = None, []
            result_spec = spec.get("result")
            if result_spec:
                requested_title = payload.get(result_spec.get("title_from_input", ""))
                title = (
                    requested_title.strip()
                    if isinstance(requested_title, str) and requested_title.strip()
                    else result_spec["title"]
                )
                resource = ctx.create_resource(result_spec["resource_type"], title, data)
                artifact = result_spec.get("text_artifact")
                if artifact:
                    text = data.get(artifact["field"])
                    require(
                        isinstance(text, str),
                        "remote_artifact_contract",
                        "远程文本产物字段不符合契约。",
                        502,
                    )
                    artifacts.append(
                        ctx.create_artifact(
                            resource["id"], artifact["name"], text, "text/plain; charset=utf-8"
                        )
                    )
            return CapabilityOutput(
                summary,
                data,
                views=[
                    {
                        "type": spec["presentation"][ctx.origin]["renderer"],
                        "data": data,
                        "capability_id": spec["id"],
                        "authorization_revision": ctx.actor.get("authorization_revision", 1),
                    }
                ],
                resource=resource,
                artifacts=artifacts,
            )

        return handle

    def refresh_workspace(
        self,
        data,
        actor,
        capability_id=None,
        authorization_revision=None,
        *,
        invocation_id=None,
        view_index=None,
    ):
        if capability_id:
            self.registry.get(capability_id, actor)
        require(
            authorization_revision is None
            or authorization_revision == actor.get("authorization_revision", 1),
            "authorization_changed",
            "这份结果的身份授权已变化，请重新发起查询。",
            403,
        )
        validate_workspace(data)
        result = copy.deepcopy(data)
        for block_index, block in enumerate(result["blocks"]):
            if (
                block["type"] == "download"
                and isinstance(invocation_id, str)
                and re.fullmatch(r"[0-9a-f]{32}", invocation_id)
                and type(view_index) is int
                and view_index >= 0
            ):
                block.pop("content_base64", None)
                block["download_url"] = (
                    f"/api/v1/invocations/{invocation_id}/workspace/{view_index}/{block_index}/download"
                )
            if block["type"] == "actions":
                permitted = []
                for action in block["items"]:
                    try:
                        spec = self.registry.get(action["capability_id"], actor)
                        allowed = spec["input_schema"].get("properties", {})
                        permitted.append(
                            {
                                **action,
                                "input": {k: v for k, v in action["input"].items() if k in allowed},
                            }
                        )
                    except PlatformError:
                        pass
                block["items"] = permitted
        return result

    def workspace_download(self, db, actor, invocation_id, view_index, block_index):
        """Read immutable result bytes after current owner, scope and schema checks."""
        actor = refresh_actor(db, actor)
        invocation = db.get(Invocation, invocation_id)
        require(invocation is not None, "not_found", "没有找到这个结果。", 404)
        require(
            invocation.actor_id == actor["id"] or actor["role"] == "admin",
            "forbidden",
            "无权下载该结果。",
            403,
        )
        if invocation.principal_snapshot.get("_access_stamp"):
            require(
                invocation.actor_id == actor["id"], "forbidden", "业务授权结果只能由本人下载。", 403
            )
        owner = db.get(User, invocation.actor_id)
        require(
            owner is not None and owner.enabled, "actor_disabled", "结果所有者当前不可用。", 403
        )
        owner_actor = user_dict(owner)
        self.check_invocation_authorization(invocation, owner_actor)
        self.registry.get(invocation.capability_id, actor)
        require(
            invocation.status == "completed" and invocation.result,
            "request_pending",
            "结果尚未完成。",
            409,
        )
        result = invocation.result
        if result.get("run_id"):
            run = db.get(Run, result["run_id"])
            require(
                run is not None and run.status == "succeeded",
                "request_pending",
                "任务尚未成功完成。",
                409,
            )
        if result.get("resource"):
            accessible(db.get(Resource, result["resource"]["id"]), actor)
        views = result.get("views", [])
        require(
            0 <= view_index < len(views) and views[view_index].get("type") == "workspace",
            "not_found",
            "下载内容不存在。",
            404,
        )
        view = views[view_index]
        revision = view.get("authorization_revision")
        require(
            bool(invocation.principal_snapshot) or revision is not None,
            "authorization_changed",
            "旧结果缺少可核验的授权记录，请重新生成。",
            403,
        )
        if revision is not None:
            require(
                type(revision) is int and revision == owner_actor.get("authorization_revision", 1),
                "authorization_changed",
                "结果对应的授权范围已变化，请重新生成。",
                403,
            )
        data = view["data"]
        validate_workspace(data)
        blocks = data["blocks"]
        require(
            0 <= block_index < len(blocks) and blocks[block_index]["type"] == "download",
            "not_found",
            "下载内容不存在。",
            404,
        )
        block = blocks[block_index]
        content = base64.b64decode(block["content_base64"], validate=True)
        audit(
            db,
            actor["id"],
            "workspace.download",
            invocation.id,
            view_index=view_index,
            block_index=block_index,
        )
        media_type = block["media_type"]
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:;[ -~]*)?", media_type):
            media_type = "application/octet-stream"
        return content, block["filename"], media_type

    def check_invocation_authorization(self, invocation, actor):
        if invocation and invocation.principal_snapshot:
            require(
                authorization_snapshot(invocation.principal_snapshot)
                == authorization_snapshot(actor),
                "authorization_changed",
                "调用的权限或所属范围已变化，请创建新的调用。",
                403,
            )
            if invocation.principal_snapshot.get("_access_stamp"):
                require(
                    invocation.principal_snapshot["_access_stamp"]
                    == self.access.authorization_stamp(actor),
                    "authorization_changed",
                    "业务授权已经变化，旧调用内容不可再访问。",
                    403,
                )
                self.registry.get(invocation.capability_id, actor)

    def _register_core(self):
        module = {
            "id": "core",
            "name": "平台共用能力",
            "version": "1.0.0",
            "source": "core",
            "description": "所有模块共用的结果、办理、历史与模型设置。",
        }
        self.registry.modules.append(module)
        obj = {"type": "object"}
        ref_props = {
            "resource_id": {"type": "string", "title": "结果编号"},
            "expected_revision": {"type": "integer", "minimum": 1, "title": "当前版本"},
        }
        specs = [
            (
                "core.workflow.navigate",
                "切换当前流程步骤",
                "在本人当前对话中切换已声明的业务步骤，仅导航，不提交业务命令。编号来自当前流程引用及步骤目录。",
                "query",
                {
                    **obj,
                    "properties": {
                        "instance_id": {"type": "string", "title": "流程实例"},
                        "step_id": {"type": "string", "title": "步骤编号"},
                    },
                    "required": ["instance_id", "step_id"],
                    "additionalProperties": False,
                },
                ["member", "admin", "system_admin"],
                lambda ctx, payload: self.workflows.navigate_capability(ctx, payload),
                "workflow",
            ),
            (
                "core.workflow.open",
                "打开步骤工作台",
                "按目录编号打开共享步骤流程，仅建立个人导航，不创建或提交业务对象。",
                "query",
                {
                    **obj,
                    "properties": {"flow_id": {"type": "string", "title": "流程编号"}},
                    "required": ["flow_id"],
                    "additionalProperties": False,
                },
                ["member", "admin", "system_admin"],
                lambda ctx, payload: self.workflows.open_capability(ctx, payload),
                "workflow",
            ),
            (
                "core.interaction.open",
                "打开共享组件",
                "按组件目录的编号打开可直接操作的共享组件；仅创建交互，不执行业务动作。",
                "query",
                {
                    **obj,
                    "properties": {
                        "component_id": {"type": "string", "title": "组件编号"},
                        "initial_input": {"type": "object", "title": "初始参数"},
                    },
                    "required": ["component_id"],
                    "additionalProperties": False,
                },
                ["member", "admin", "system_admin"],
                lambda ctx, payload: self.interactions.open_capability(ctx, payload),
                "interaction",
            ),
            (
                "core.resource.submit",
                "提交复核",
                "把已有结果交给管理员复核。",
                "command",
                {
                    **obj,
                    "properties": ref_props,
                    "required": ["resource_id", "expected_revision"],
                    "additionalProperties": False,
                },
                ["member", "admin"],
                self._submit,
                "resource",
            ),
            (
                "core.resource.decide",
                "办理复核",
                "管理员接受或退回一个已经送审的结果。",
                "command",
                {
                    **obj,
                    "properties": {
                        **ref_props,
                        "decision": {
                            "type": "string",
                            "enum": ["accept", "return"],
                            "title": "处理决定",
                        },
                        "note": {"type": "string", "maxLength": 2000, "title": "处理说明"},
                    },
                    "required": ["resource_id", "expected_revision", "decision"],
                    "additionalProperties": False,
                },
                ["admin"],
                self._decide,
                "resource",
            ),
            (
                "core.resource.get",
                "查看结果",
                "读取一个结果及其历史版本。",
                "query",
                {
                    **obj,
                    "properties": {"resource_id": ref_props["resource_id"]},
                    "required": ["resource_id"],
                    "additionalProperties": False,
                },
                ["member", "admin"],
                self._get_resource,
                "resource",
            ),
            (
                "core.history",
                "查看工作记录",
                "查看当前用户可访问的结果与运行记录。",
                "query",
                {**obj, "properties": {}, "additionalProperties": False},
                ["member", "admin"],
                self._history,
                "history",
            ),
            (
                "core.registry",
                "查看模块契约",
                "查看当前身份可用的模块、输入输出和呈现约定。",
                "query",
                {**obj, "properties": {}, "additionalProperties": False},
                ["member", "admin"],
                self._registry_info,
                "json",
            ),
            (
                "core.models.configure",
                "打开模型配置",
                "在当前窗体配置模型与测试连接；密钥只在密码输入框填写。",
                "query",
                {**obj, "properties": {}, "additionalProperties": False},
                ["admin"],
                self._models,
                "model-settings",
            ),
            (
                "core.services.configure",
                "打开服务接入",
                "在当前窗体连接远程微服务，预览并发布其能力。",
                "query",
                {**obj, "properties": {}, "additionalProperties": False},
                ["admin"],
                self._services,
                "remote-services",
            ),
            (
                "core.module.manage",
                "打开模块管理",
                "查看本地与远程模块、页面工具、对话能力和运行状态，并管理发布。",
                "query",
                {**obj, "properties": {}, "additionalProperties": False},
                ["admin"],
                self._modules,
                "module-management",
            ),
            (
                "core.page.open",
                "打开模块页面",
                "在当前窗体打开模块已注册的隔离网页工具；模块编号和工具编号必须来自目录。",
                "query",
                {
                    **obj,
                    "properties": {
                        "module_id": {"type": "string", "title": "模块编号"},
                        "tool_id": {"type": "string", "title": "页面工具编号"},
                    },
                    "required": ["module_id", "tool_id"],
                    "additionalProperties": False,
                },
                ["member", "admin"],
                self._page,
                "module-page",
            ),
        ]
        for cap_id, title, desc, kind, schema, roles, handler, renderer in specs:
            if renderer in ("model-settings", "remote-services", "module-management"):
                roles = [*roles, "system_admin"]
            spec = {
                "id": cap_id,
                "version": "1.0.0",
                "title": title,
                "description": desc,
                "kind": kind,
                "input_schema": schema,
                "output_schema": obj,
                "roles": roles,
                "execution": {"mode": "sync", "confirmation": kind == "command"},
                "presentation": {
                    "page": {"renderer": renderer},
                    "conversation": {"renderer": renderer},
                },
            }
            self.registry.register(module, spec, handler)

    def _transition(self, ctx, payload, action):
        resource = ctx.session.get(Resource, payload["resource_id"])
        accessible(resource, ctx.actor)
        transition = self.flow["transitions"][action]
        require(
            has_role(ctx.actor, transition["roles"]), "forbidden", "当前身份不能办理此动作。", 403
        )
        require(
            resource.revision == payload["expected_revision"],
            "revision_conflict",
            "结果已更新，请重新查看当前版本。",
            409,
        )
        require(
            resource.status in transition["from"], "state_conflict", "当前状态不允许该操作。", 409
        )
        note = payload.get("note", "").strip()
        require(
            action != "return" or bool(note), "note_required", "退回时请填写需要调整的说明。", 422
        )
        previous = resource.revision
        changed = ctx.session.execute(
            update(Resource)
            .where(Resource.id == resource.id, Resource.revision == previous)
            .values(revision=previous + 1, status=transition["to"], updated_at=now())
        )
        require(changed.rowcount == 1, "revision_conflict", "其他窗口已经修改此结果。", 409)
        ctx.session.refresh(resource)
        ctx.session.add(
            ResourceVersion(
                resource_id=resource.id,
                revision=resource.revision,
                data=resource.data,
                status=resource.status,
                note=note or {"submit": "提交复核", "accept": "接受结果"}[action],
                actor_id=ctx.actor["id"],
            )
        )
        ctx.session.flush()
        result = resource_dict(ctx.session, resource)
        return CapabilityOutput(
            summary={
                "submit": "已提交复核，管理员可以接受或退回。",
                "accept": "复核已接受。",
                "return": "结果已退回，可按说明调整后重新提交。",
            }[action],
            data={
                "resource_id": resource.id,
                "status": resource.status,
                "revision": resource.revision,
            },
            resource=result,
            views=[{"type": "resource", "data": result}],
        )

    def _submit(self, ctx, payload):
        return self._transition(ctx, payload, "submit")

    def _decide(self, ctx, payload):
        return self._transition(ctx, payload, payload["decision"])

    def _get_resource(self, ctx, payload):
        resource = ctx.session.get(Resource, payload["resource_id"])
        accessible(resource, ctx.actor)
        result = resource_dict(ctx.session, resource)
        return CapabilityOutput(
            "已读取结果及当前版本。",
            {"resource_id": resource.id},
            resource=result,
            views=[{"type": "resource", "data": result}],
        )

    def _history(self, ctx, payload):
        data = self.history_data(ctx.session, ctx.actor)
        return CapabilityOutput(
            "已读取最近的工作记录。", data, views=[{"type": "history", "data": data}]
        )

    def history_data(self, db, actor):
        with self.sessions() as identity_db:
            actor = refresh_actor(identity_db, actor)
        resources = db.query(Resource)
        runs = db.query(Run)
        if actor["role"] != "admin":
            resources, runs = resources.filter_by(owner_id=actor["id"]), runs.filter_by(
                actor_id=actor["id"]
            )
        visible = []
        for resource in resources.order_by(Resource.updated_at.desc()).limit(20):
            try:
                accessible(resource, actor)
            except PlatformError:
                continue
            visible.append(resource_dict(db, resource))
        return {
            "resources": visible,
            "runs": self.visible_runs(db, runs.order_by(Run.created_at.desc()).limit(20), actor),
            "audit": self.audit_data(db, actor, 20),
        }

    def visible_runs(self, db, rows, actor):
        visible = []
        for row in rows:
            try:
                visible.append(self.current_run_view(db, row, actor))
            except PlatformError as exc:
                if exc.status not in (403, 404):
                    raise
        return visible

    def audit_data(self, db, actor, limit=150):
        with self.sessions() as identity_db:
            actor = refresh_actor(identity_db, actor)
        query = db.query(Audit)
        if actor["role"] != "admin":
            query = query.filter_by(actor_id=actor["id"])
        users = {u.id: u.name for u in db.query(User)}
        return [
            {
                "id": a.id,
                "action": a.action,
                "target": a.target,
                "actor": users.get(a.actor_id, a.actor_id),
                "created_at": a.created_at,
                "detail": (
                    {}
                    if a.action.startswith("identity.") and not can_manage_platform(actor)
                    else a.detail
                ),
            }
            for a in query.order_by(Audit.created_at.desc()).limit(limit)
        ]

    def _registry_info(self, ctx, payload):
        data = self.public_registry(ctx.actor)
        return CapabilityOutput(
            "当前可用的模块与契约如下。",
            data,
            views=[{"type": "json", "source": "registry", "data": data}],
        )

    def _models(self, ctx, payload):
        return CapabilityOutput(
            "请在下方配置模型。API Key 只在密码输入框填写。",
            {},
            views=[{"type": "model-settings", "data": {}}],
        )

    def _services(self, ctx, payload):
        return CapabilityOutput(
            "保存服务连接，发现并预览能力后，再启用到页面和对话。",
            {},
            views=[{"type": "remote-services", "data": {}}],
        )

    def _modules(self, ctx, payload):
        return CapabilityOutput(
            "模块发布状态、运行状态和两端入口在这里统一管理。",
            {},
            views=[{"type": "module-management", "data": {}}],
        )

    def _page(self, ctx, payload):
        page = self.module_page(payload["module_id"], payload["tool_id"], ctx.actor)
        reference = {"module_id": page["module_id"], "tool_id": page["tool_id"]}
        return CapabilityOutput(
            "已打开已注册的网页工具；独立页面内的操作不会自动保存为平台结果。",
            reference,
            views=[{"type": "module-page", "data": reference}],
        )

    def context(self, db, actor, run_id=None, invocation_id=None):
        invocation = db.get(Invocation, invocation_id) if invocation_id else None
        return CapabilityContext(
            session=db,
            actor=actor,
            workspace_id="local",
            gateway=self.gateway,
            run_id=run_id,
            invocation_id=invocation_id,
            origin=invocation.origin if invocation else "page",
            access=self.access,
            access_stamp=(
                invocation.principal_snapshot.get("_access_stamp")
                if invocation and invocation.principal_snapshot
                else None
            ),
        )

    async def execute(self, db, actor, capability_id, payload, run_id=None, invocation_id=None):
        """选择本地处理器或远程接口，并按当前能力声明验证结果。"""
        spec = self.registry.get(capability_id, actor)
        self.check_available(spec)
        self.registry.validate(spec["input_schema"], payload)
        validate_files(spec["input_schema"], payload)
        try:
            output = self.registry.handlers[capability_id](
                self.context(db, actor, run_id, invocation_id), payload
            )
            if inspect.isawaitable(output):
                output = await output
        except Exception as exc:
            from .model_gateway import ModelGatewayError

            if isinstance(exc, (PlatformError, ModelGatewayError)):
                raise
            raise PlatformError("handler_failed", "模块处理失败，本次修改未保存。", 500) from None
        require(
            isinstance(output, CapabilityOutput),
            "handler_contract",
            "模块处理器返回了不符合契约的结果。",
            500,
        )
        self.registry.validate(spec["output_schema"], output.data)
        result = asdict(output)
        if output.resource and not any(v.get("type") == "resource" for v in result["views"]):
            resource = db.get(Resource, output.resource["id"])
            result["resource"] = resource_dict(db, resource)
            result["views"].append({"type": "resource", "data": result["resource"]})
        if not result["views"]:
            result["views"] = [
                {
                    "type": spec["presentation"][
                        self.context(db, actor, run_id, invocation_id).origin
                    ]["renderer"],
                    "data": result["data"],
                }
            ]
        for view_index, view in enumerate(result["views"]):
            if view.get("type") == "workspace":
                validate_workspace(view["data"])
                view.update(
                    capability_id=capability_id,
                    authorization_revision=actor.get("authorization_revision", 1),
                    invocation_id=invocation_id,
                    view_index=view_index,
                )
        return result

    def conversation_check(self, db, conversation_id, actor):
        conv = db.get(Conversation, conversation_id)
        require(
            conv is not None and conv.owner_id == actor["id"],
            "conversation_scope",
            "无权访问此对话。",
            403,
        )
        return conv

    def append_invocation_message(self, db, invocation, result):
        if invocation.conversation_id:
            # References are refreshed on read. No business action is replayed.
            views = result.get("views", [])
            if result.get("run_id"):
                views = [
                    {"type": "task", "data": {"run_id": result["run_id"], "id": result["run_id"]}}
                ]
            db.add(
                Message(
                    conversation_id=invocation.conversation_id,
                    role="assistant",
                    content=result.get("summary", ""),
                    views=views,
                    request_key="invoke-" + invocation.id,
                )
            )

    async def invoke(
        self,
        actor,
        capability_id,
        payload,
        key,
        origin="page",
        conversation_id=None,
        *,
        interaction_guard=None,
        workflow_guard=None,
    ):
        """统一调用入口：重新授权、核对幂等请求，在事务内保存效果与回执。"""
        spec = self.registry.get(capability_id, actor)
        self.registry.validate(spec["input_schema"], payload)
        validate_files(spec["input_schema"], payload)
        maximum = MAX_INVOKE_BYTES if spec.get("remote_protocol") == "1.2" else 512_000
        require(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= maximum,
            "request_size",
            "输入超过此协议的大小限制。",
            413,
        )

        def includes_secret(value):
            if isinstance(value, str):
                return self.redact(value) != value
            if isinstance(value, dict):
                return any(includes_secret(k) or includes_secret(v) for k, v in value.items())
            if isinstance(value, list):
                return any(includes_secret(v) for v in value)
            return False

        require(
            not includes_secret(payload),
            "secret_in_input",
            "内容包含已配置的模型密钥。请移除后重试；密钥只能填写在模型配置的密码输入框。",
            422,
        )
        require(bool(key) and len(key) <= 100, "idempotency_key", "请提供有效请求标识。", 422)
        digest = fingerprint(
            {
                "capability": capability_id,
                "version": self.execution_revision(spec),
                "input": payload,
                "conversation": conversation_id,
                **({"interaction": interaction_guard} if interaction_guard else {}),
                **({"workflow": workflow_guard} if workflow_guard else {}),
            }
        )
        async with self.lock:
            current_spec = self.registry.get(capability_id, actor)
            require(
                self.execution_revision(current_spec) == self.execution_revision(spec),
                "version_changed",
                "能力连接刚刚变化，请重新查看后运行。",
                409,
            )
            with self.sessions() as db:
                actor = refresh_actor(db, actor, require_unchanged=True)
                if conversation_id:
                    self.conversation_check(db, conversation_id, actor)
                existing = (
                    db.query(Invocation)
                    .filter_by(actor_id=actor["id"], idempotency_key=key)
                    .first()
                )
                if existing:
                    require(
                        existing.input_hash == digest,
                        "idempotency_conflict",
                        "相同请求标识不能用于不同内容。",
                        409,
                    )
                    self.check_invocation_authorization(existing, actor)
                    return self.refresh_result(db, existing.result, actor)
                self.check_available(current_spec)
                if interaction_guard:
                    self.interactions.begin_action(
                        db, actor, interaction_guard, current_spec, payload
                    )
                if workflow_guard:
                    self.workflows.begin_action(db, actor, workflow_guard, current_spec, payload)
                invocation = Invocation(
                    actor_id=actor["id"],
                    capability_id=capability_id,
                    capability_version=self.execution_revision(spec),
                    idempotency_key=key,
                    input_hash=digest,
                    input=payload,
                    origin=origin,
                    conversation_id=conversation_id,
                    status="accepted",
                    principal_snapshot=self.access_snapshot(actor, spec),
                )
                db.add(invocation)
                db.flush()
                if spec["execution"]["mode"] == "background":
                    run = Run(
                        invocation_id=invocation.id,
                        actor_id=actor["id"],
                        capability_id=capability_id,
                        capability_version=self.execution_revision(spec),
                        status="queued",
                        input=payload,
                    )
                    db.add(run)
                    db.flush()
                    result = {
                        "id": invocation.id,
                        "capability_id": capability_id,
                        "invocation_status": "accepted",
                        "run_status": "queued",
                        "run_id": run.id,
                        "summary": "任务已排队，可离开当前页面后再查看。",
                        "views": [{"type": "task", "data": run_dict(run)}],
                        "artifacts": [],
                    }
                else:
                    output = await self.execute(
                        db, actor, capability_id, payload, invocation_id=invocation.id
                    )
                    with self.sessions() as check:
                        refresh_actor(check, actor, require_unchanged=True)
                    self.check_invocation_authorization(invocation, actor)
                    result = {
                        "id": invocation.id,
                        "capability_id": capability_id,
                        "invocation_status": "completed",
                        **output,
                    }
                    invocation.status = "completed"
                invocation.result = result
                if interaction_guard:
                    # refresh_actor deliberately expires loaded ORM state. Persist
                    # the receipt before the component re-authorizes its update.
                    db.flush()
                    self.interactions.finish_action(db, actor, interaction_guard, result)
                if workflow_guard:
                    db.flush()
                    self.workflows.finish_action(db, actor, workflow_guard, result)
                audit(
                    db,
                    actor["id"],
                    capability_id,
                    invocation.id,
                    origin=origin,
                    capability_version=spec["version"],
                    input_hash=digest,
                    **(
                        {
                            "service_id": spec["service_id"],
                            "binding_revision": spec["binding_revision"],
                        }
                        if spec.get("service_id")
                        else {}
                    ),
                )
                if not interaction_guard and not workflow_guard:
                    self.append_invocation_message(db, invocation, result)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raise PlatformError(
                        "concurrent_request", "请求正在另一个窗口处理中，请稍后重试。", 409
                    )
                return self.refresh_result(db, result, actor)

    def refresh_result(self, db, result, actor, *, invocation_id=None):
        if not result:
            raise PlatformError("request_pending", "请求仍在处理中。", 409)
        result = copy.deepcopy(result)
        for view_index, view in enumerate(result.get("views", [])):
            if view.get("type") == "workspace":
                view["data"] = self.refresh_workspace(
                    view["data"],
                    actor,
                    view.get("capability_id"),
                    view.get("authorization_revision"),
                    invocation_id=invocation_id or result.get("id") or view.get("invocation_id"),
                    view_index=view_index,
                )
        if result.get("run_id"):
            run = db.get(Run, result["run_id"])
            require(
                run.actor_id == actor["id"] or actor["role"] == "admin",
                "forbidden",
                "无权读取该任务。",
                403,
            )
            result["run_status"] = run.status
            result["views"] = [{"type": "task", "data": self.current_run_view(db, run, actor)}]
        if result.get("resource"):
            resource = db.get(Resource, result["resource"]["id"])
            accessible(resource, actor)
            result["resource"] = resource_dict(db, resource)
            result["views"] = [
                (
                    {"type": "resource", "data": result["resource"]}
                    if v.get("type") == "resource" and v.get("data", {}).get("id") == resource.id
                    else v
                )
                for v in result.get("views", [])
            ]
        if result.get("capability_id") == "core.history" or any(
            v.get("type") == "history" for v in result.get("views", [])
        ):
            result["data"] = self.history_data(db, actor)
            result["views"] = [
                ({"type": "history", "data": result["data"]} if v.get("type") == "history" else v)
                for v in result.get("views", [])
            ]
        if result.get("capability_id") == "core.registry":
            result["data"] = self.public_registry(actor)
            result["views"] = [{"type": "json", "source": "registry", "data": result["data"]}]
        return result

    def current_run_view(self, db, run, actor):
        actor = refresh_actor(db, actor)
        require(
            run is not None and (run.actor_id == actor["id"] or actor["role"] == "admin"),
            "forbidden",
            "无权读取该任务。",
            403,
        )
        invocation = db.get(Invocation, run.invocation_id)
        if invocation and invocation.principal_snapshot.get("_access_stamp"):
            require(
                run.actor_id == actor["id"],
                "forbidden",
                "业务授权任务不能通过旧管理员全读规则访问。",
                403,
            )
        result = run_dict(run)
        if run.actor_id == actor["id"]:
            try:
                self.check_invocation_authorization(db.get(Invocation, run.invocation_id), actor)
            except PlatformError:
                # Owners can still observe terminal status after revocation, but
                # no previously authorized input or output crosses this boundary.
                return {
                    **result,
                    "input": {},
                    "result": None,
                    "error": {
                        "code": "authorization_changed",
                        "message": "权限或所属范围已变化，历史内容不可再访问。",
                    },
                }
        if run.result:
            result["result"] = self.refresh_result(
                db, run.result, actor, invocation_id=run.invocation_id
            )
        return result

    async def start(self):
        with self.sessions.begin() as db:
            # Remote model response might have been generated before a crash. Do
            # not silently replay a paid call; offer explicit retry instead.
            for run in db.query(Run).filter_by(status="running"):
                run.status, run.updated_at = "failed", now()
                run.error = {
                    "code": "interrupted",
                    "message": "上次运行被中断，结果尚未提交。可手动重试。",
                }
                audit(db, run.actor_id, "run.interrupted", run.id)
        self.worker_task = asyncio.create_task(self.worker())

    async def stop(self):
        if self.current_job:
            self.current_job.cancel()
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

    async def worker(self):
        while True:
            with self.sessions.begin() as db:
                run = db.query(Run).filter_by(status="queued").order_by(Run.created_at).first()
                run_id = run.id if run else None
                if run:
                    db.execute(
                        update(Run)
                        .where(Run.id == run_id, Run.status == "queued")
                        .values(
                            status="running", attempt=run.attempt + 1, progress=10, updated_at=now()
                        )
                    )
            if not run_id:
                await asyncio.sleep(0.4)
                continue
            self.current_run = run_id
            self.current_job = asyncio.create_task(self.process_run(run_id))
            try:
                await self.current_job
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self.current_run, self.current_job = None, None

    async def process_run(self, run_id):
        try:
            with self.sessions() as db:
                run = db.get(Run, run_id)
                user = db.get(User, run.actor_id)
                require(
                    user is not None and user.enabled, "actor_disabled", "任务执行者已不可用。", 403
                )
                actor = user_dict(user)
                self.check_invocation_authorization(db.get(Invocation, run.invocation_id), actor)
                spec = self.registry.get(run.capability_id, actor)
                require(
                    self.execution_revision(spec) == run.capability_version,
                    "version_changed",
                    "能力或服务连接版本已经变化，请创建新任务。",
                    409,
                )
                output = await self.execute(
                    db, actor, run.capability_id, run.input, run.id, run.invocation_id
                )
                # check cancellation in a fresh read before publishing output.
                with self.sessions() as check:
                    latest = check.get(Run, run.id)
                    if latest.status == "cancelled":
                        db.rollback()
                        return
                    current_user = check.get(User, run.actor_id)
                    require(
                        current_user is not None and current_user.enabled,
                        "actor_disabled",
                        "任务执行者已不可用，结果未发布。",
                        403,
                    )
                    self.check_invocation_authorization(
                        db.get(Invocation, run.invocation_id), user_dict(current_user)
                    )
                    current_spec = self.registry.get(run.capability_id, user_dict(current_user))
                    require(
                        self.execution_revision(current_spec) == run.capability_version,
                        "version_changed",
                        "执行期间能力或服务连接已变化，结果未发布。",
                        409,
                    )
                run.status, run.progress, run.updated_at = "succeeded", 100, now()
                run.result, run.error = output, None
                invocation = db.get(Invocation, run.invocation_id)
                invocation.status = "completed"
                invocation.result = {
                    "id": invocation.id,
                    "capability_id": run.capability_id,
                    "invocation_status": "completed",
                    "run_id": run.id,
                    "run_status": "succeeded",
                    **output,
                }
                audit(db, actor["id"], "run.succeeded", run.id, attempt=run.attempt)
                db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from .model_gateway import ModelGatewayError

            if isinstance(exc, (PlatformError, ModelGatewayError)):
                error = {"code": exc.code, "message": exc.message}
            else:
                error = {
                    "code": "execution_failed",
                    "message": "模块运行失败，结果未提交。请检查模块契约或联系管理员。",
                }
            with self.sessions.begin() as db:
                run = db.get(Run, run_id)
                if run and run.status != "cancelled":
                    run.status, run.error, run.updated_at = "failed", error, now()
                    audit(db, run.actor_id, "run.failed", run.id, error=error, attempt=run.attempt)

    async def control_run(self, actor, run_id, action):
        async with self.lock:
            with self.sessions.begin() as db:
                actor = refresh_actor(db, actor, require_unchanged=True)
                run = db.get(Run, run_id)
                require(run is not None, "not_found", "任务不存在。", 404)
                require(
                    run.actor_id == actor["id"] or actor["role"] == "admin",
                    "forbidden",
                    "无权操作此任务。",
                    403,
                )
                invocation = db.get(Invocation, run.invocation_id)
                if invocation and invocation.principal_snapshot.get("_access_stamp"):
                    require(
                        run.actor_id == actor["id"],
                        "forbidden",
                        "只能操作本人的业务授权任务。",
                        403,
                    )
                if action == "retry":
                    owner = db.get(User, run.actor_id)
                    require(
                        owner is not None and owner.enabled,
                        "actor_disabled",
                        "任务所有者不可用。",
                        403,
                    )
                    self.check_invocation_authorization(
                        db.get(Invocation, run.invocation_id), user_dict(owner)
                    )
                    self.registry.get(run.capability_id, user_dict(owner))
                    require(run.status == "failed", "state_conflict", "只有失败任务可以重试。", 409)
                    run.status, run.error, run.progress = "queued", None, 0
                else:
                    require(
                        run.status in ("queued", "running"), "state_conflict", "任务已经结束。", 409
                    )
                    run.status = "cancelled"
                run.updated_at = now()
                audit(db, actor["id"], "run." + action, run.id)
                db.flush()
                result = self.current_run_view(db, run, actor)
            if action == "cancel" and self.current_run == run_id and self.current_job:
                self.current_job.cancel()
            return result
