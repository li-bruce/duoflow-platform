"""Trusted local registration and validated remote capability metadata.

This catalog binds identifiers to handlers; it does not own business data or
authorize individual records. Runtime and module repositories perform those
checks at execution. Remote metadata never supplies executable Python or UI code.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import PlatformError, require
from .module_contract import normalize_surfaces
from .identity import capability_allowed
from .workspace_contract import ROLE_IDS

RENDERERS = [
    "text-result",
    "resource",
    "task",
    "form",
    "model-settings",
    "remote-services",
    "module-management",
    "module-page",
    "history",
    "json",
    "workspace",
    "agent-flow",
    "interaction",
    "workflow",
]


def inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    require(target.is_relative_to(root.resolve()), "manifest_path", "注册包路径超出范围。")
    return target


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.modules: list[dict] = []
        self.capabilities: dict[str, dict] = {}
        self.handlers: dict[str, object] = {}
        self.workflow_adapter_factories: dict[str, object] = {}
        self.availability = None
        self.access_allowed = None

    def register(self, module: dict, spec: dict, handler):
        """登记能力和处理器；校验输入输出及两端呈现，暂不执行任何业务。"""
        spec = {**spec, "module_id": module["id"]}
        require(
            bool(re.fullmatch(r"[a-z][a-z0-9_.-]{2,120}", spec["id"])),
            "manifest_id",
            "能力标识格式错误。",
        )
        require(spec["id"] not in self.capabilities, "manifest_duplicate", "能力标识重复。")
        require(spec.get("kind") in ("query", "command"), "manifest_kind", "能力类型错误。")
        require(
            spec.get("execution", {}).get("mode") in ("sync", "background"),
            "manifest_execution",
            "能力执行方式错误。",
        )
        require(
            set(spec.get("roles", [])) <= set(ROLE_IDS) and bool(spec.get("roles")),
            "manifest_roles",
            "能力未声明有效权限。",
        )
        for host in ("page", "conversation"):
            require(
                spec.get("presentation", {}).get(host, {}).get("renderer") in RENDERERS,
                "manifest_presentation",
                "能力需要同时声明页面和对话呈现器。",
            )
        for key in ("input_schema", "output_schema"):
            schema = spec[key]
            Draft202012Validator.check_schema(schema)
            self._check_refs(schema)
        self.capabilities[spec["id"]] = spec
        self.handlers[spec["id"]] = handler

    @staticmethod
    def _check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                require(
                    str(value["$ref"]).startswith("#"),
                    "manifest_schema",
                    "第一版 Schema 仅支持包内展开或本地片段引用。",
                )
            for child in value.values():
                Registry._check_refs(child)
        elif isinstance(value, list):
            for child in value:
                Registry._check_refs(child)

    def load(self):
        extensions = self.root / "extensions"
        config = json.loads((extensions / "installed.json").read_text(encoding="utf-8-sig"))
        for folder in config["modules"]:
            base = inside(extensions, folder)
            module = json.loads((base / "manifest.json").read_text(encoding="utf-8-sig"))
            require(
                not any(m["id"] == module["id"] for m in self.modules),
                "manifest_duplicate",
                "模块标识重复。",
            )
            public = {k: module[k] for k in ("id", "version", "name", "description")}
            public["source"] = "local"
            self.modules.append(public)
            for raw in module["capabilities"]:
                spec = dict(raw)
                for key in ("input_schema", "output_schema"):
                    spec[key] = (
                        json.loads(inside(base, spec[key]).read_text(encoding="utf-8-sig"))
                        if isinstance(spec[key], str)
                        else spec[key]
                    )
                filename, name = spec.pop("handler").split(":")
                require(
                    bool(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", filename + name)),
                    "manifest_handler",
                    "处理器标识错误。",
                )
                path = inside(base, filename + ".py")
                import_spec = importlib.util.spec_from_file_location(
                    "extension_" + module["id"].replace(".", "_") + "_" + filename, path
                )
                loaded = importlib.util.module_from_spec(import_spec)
                import_spec.loader.exec_module(loaded)
                handler = getattr(loaded, name)
                require(callable(handler), "manifest_handler", "处理器不可调用。")
                self.register(public, spec, handler)
            try:
                public["surfaces"] = normalize_surfaces(
                    module["id"],
                    list(s for s in self.capabilities.values() if s["module_id"] == module["id"]),
                    module.get("surfaces"),
                    allow_skills=True,
                )
            except ValueError:
                raise PlatformError(
                    "manifest_surfaces", "模块的页面工具与对话能力声明无效。", 422
                ) from None
            if "workflow_adapter" in module:
                self.workflow_adapter_factories[module["id"]] = self._workflow_factory(
                    base, module["id"], module["workflow_adapter"]
                )

    @staticmethod
    def _workflow_factory(base, module_id, reference):
        require(
            isinstance(reference, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*", reference),
            "manifest_workflow_adapter",
            "本地流程适配器必须为包内文件名:工厂名。",
            422,
        )
        filename, symbol = reference.split(":")
        path = inside(base, filename + ".py")
        require(path.is_file(), "manifest_workflow_adapter", "本地流程适配器文件不存在。", 422)
        try:
            spec = importlib.util.spec_from_file_location(
                "workflow_adapter_" + module_id.replace(".", "_"), path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = getattr(module, symbol, None)
        except Exception:
            raise PlatformError(
                "manifest_workflow_adapter", "本地流程适配器无法加载。", 422
            ) from None
        require(callable(factory), "manifest_workflow_adapter", "本地流程适配器工厂不可调用。", 422)
        return factory

    def get(self, capability_id: str, actor: dict):
        spec = self.capabilities.get(capability_id)
        require(spec is not None, "capability_missing", "没有找到该能力。", 404)
        require(self.allowed(actor, spec), "forbidden", "当前身份没有使用该能力的权限。", 403)
        return spec

    def allowed(self, actor, spec):
        # A registered access module is governed by its permissions, never by a
        # fallback to the legacy global role list. Object checks remain mandatory.
        decision = self.access_allowed(actor, spec) if self.access_allowed else None
        return capability_allowed(actor, spec) if decision is None else decision

    def validate(self, schema: dict, value: dict):
        """按公开 Schema 检查输入输出；错误只返回字段位置和规则名称。"""
        errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
        if errors:
            # Do not echo input values, potentially credentials, into error text.
            details = [
                {"path": ".".join(map(str, e.path)), "rule": e.validator} for e in errors[:15]
            ]
            raise PlatformError(
                "schema_validation", "输入或输出不符合模块契约，请检查字段。", 422, details
            )

    def public(self, actor: dict):
        specs, availability = [], {}
        for spec in self.capabilities.values():
            if not self.allowed(actor, spec):
                continue
            extra = {}
            if self.availability:
                key = spec.get("service_id") or "__local__"
                if key not in availability:
                    availability[key] = self.availability(spec)
                extra["availability"] = availability[key]
            specs.append({**spec, **extra})
        modules = []
        for module in self.modules:
            caps = [s for s in specs if s["module_id"] == module["id"]]
            ids = {s["id"] for s in caps}
            original = module.get("surfaces") or normalize_surfaces(
                module["id"],
                [s for s in self.capabilities.values() if s["module_id"] == module["id"]],
            )
            page_tools = []
            for page in original["page_tools"]:
                visible = [cap_id for cap_id in page["capability_ids"] if cap_id in ids]
                if visible and (
                    page["kind"] == "form" or len(visible) == len(page["capability_ids"])
                ):
                    page_tools.append({**page, "capability_ids": visible})
            surfaces = {
                "page_tools": page_tools,
                "conversation": {
                    "capability_ids": [
                        i for i in original["conversation"]["capability_ids"] if i in ids
                    ],
                    "starter_prompts": (
                        original["conversation"]["starter_prompts"]
                        if set(original["conversation"]["capability_ids"]) <= ids
                        else []
                    ),
                },
            }
            if "skills" in original["conversation"]:
                # Retain required steps: removing an unavailable review step
                # would silently turn a workflow into a different workflow.
                surfaces["conversation"]["skills"] = [
                    skill
                    for skill in original["conversation"]["skills"]
                    if skill["entry_capability"] in ids
                ]
            modules.append({**module, "surfaces": surfaces, "capabilities": caps})
        return {
            "modules": [m for m in modules if m["capabilities"]],
            "capabilities": specs,
            "renderers": RENDERERS,
        }
