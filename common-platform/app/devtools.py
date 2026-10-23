"""Read-only declaration inspection and owner-scoped live workflow observation.

This is not a dynamic plugin installer, state-machine executor or business event
store. A diagram declares possibilities; only module projections report facts.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable

from .db import WorkflowObservation, now
from .errors import PlatformError, require
from .identity import can_manage_platform, refresh_actor
from .interactions import digest
from .registry import Registry, inside
from .state_machines import validate_state_machine
from .workflows import Workflows


MAX_DECLARATION_BYTES = 256 * 1024
MAX_HISTORY = 100
HISTORY_NOTE = (
    "宿主观察历史，不等于完整业务事件；仅在读取调试视图时采样，且只显示当前业务对象的观察。"
)
ROOT = Path(__file__).resolve().parents[1]


def diagnostic(code, message, severity="error", path=None):
    return {
        "code": code,
        "message": message,
        "severity": severity,
        **({"path": path} if path else {}),
    }


def declaration(value):
    return deepcopy(
        {key: val for key, val in value.items() if key not in ("pack", "definition_hash")}
    )


def bounded_json(value):
    """Bound validation work and prohibit network JSON-Schema references."""
    count = 0

    def visit(item, depth=0):
        nonlocal count
        count += 1
        require(
            depth <= 40 and count <= 20000, "devtools_complexity", "声明结构过深或节点过多。", 422
        )
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    require(
                        isinstance(child, str) and child.startswith("#"),
                        "devtools_reference",
                        "声明只允许包内 JSON 引用，不读取文件或网络。",
                        422,
                    )
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    require(
        len(encoded.encode("utf-8")) <= MAX_DECLARATION_BYTES,
        "devtools_size",
        "声明超过 256 KiB。",
        413,
    )
    return encoded


class Devtools:
    def __init__(self, runtime):
        self.runtime = runtime
        self._observation_lock = threading.RLock()

    def authorize(self, actor):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
        require(can_manage_platform(actor), "forbidden", "调试工作台需要平台管理权限。", 403)
        return actor

    def _reference_diagnostics(self, kind, definition):
        result = []
        if kind == "workflow":
            provider = definition["provider"]
            refs = [
                (provider[key], None, "provider." + key)
                for key in ("read_capability", "upload_capability", "jobs_capability")
                if key in provider
            ]
            refs.extend(
                (
                    action["capability_id"],
                    action["capability_version"],
                    f"steps.{step['id']}.actions.{action['id']}",
                )
                for step in definition["steps"]
                for action in step["actions"]
            )
            if "state_machine" not in definition:
                result.append(
                    diagnostic(
                        "state_machine_absent",
                        "未声明业务状态机。步骤导航不能代替业务状态图。",
                        "info",
                    )
                )
        else:
            refs = [
                (action["capability_id"], action["capability_version"], "actions." + action["id"])
                for action in definition["actions"]
            ]
        for capability_id, version, path in refs:
            spec = self.runtime.registry.capabilities.get(capability_id)
            if spec is None:
                result.append(
                    diagnostic(
                        "capability_missing",
                        "引用能力尚未发布或已停用：" + capability_id,
                        path=path,
                    )
                )
            elif version is not None and spec["version"] != version:
                result.append(
                    diagnostic(
                        "capability_version",
                        "引用能力的版本与当前注册版本不同：" + capability_id,
                        path=path,
                    )
                )
            elif not self.runtime.availability(spec)["available"]:
                result.append(
                    diagnostic(
                        "backend_unavailable",
                        "引用能力的服务当前不可用：" + capability_id,
                        "warning",
                        path,
                    )
                )
        return result

    def _frontend(self, kind, value):
        pack = value["pack"]
        folder, filename = (
            ("flows", "steps.tsx") if kind == "workflow" else ("components", "Component.tsx")
        )
        source_present = inside(self.runtime.root / folder, f"packs/{pack}/{filename}").is_file()
        # Source installation is not proof that the running browser build has it.
        return {
            "declaration_loaded": True,
            "pack_installed": True,
            "source_present": source_present,
        }

    def catalog(self, actor):
        self.authorize(actor)
        modules = self.runtime.modules()["items"]
        # Runtime-only synthetic/custom registries may add trusted declarations.
        module_ids = {m["id"] for m in modules}
        modules.extend(
            {
                **m,
                "enabled": True,
                "capabilities": [
                    s
                    for s in self.runtime.registry.capabilities.values()
                    if s["module_id"] == m["id"]
                ],
            }
            for m in self.runtime.registry.modules
            if m["id"] not in module_ids
        )
        items = {"workflows": [], "interactions": []}
        for kind, definitions, key in [
            ("workflow", self.runtime.workflows.definitions, "workflows"),
            ("interaction", self.runtime.interactions.definitions, "interactions"),
        ]:
            for value in definitions.values():
                definition = declaration(value)
                actions = (
                    [a for s in value["steps"] for a in s["actions"]]
                    if kind == "workflow"
                    else value["actions"]
                )
                ids = {a["capability_id"] for a in actions}
                if kind == "workflow":
                    ids.update(
                        value["provider"][k]
                        for k in ("read_capability", "upload_capability", "jobs_capability")
                        if k in value["provider"]
                    )
                diagnostics = self._reference_diagnostics(kind, definition)
                registration = self._frontend(kind, value)
                registration.update(
                    backend_available=bool(ids)
                    and all(
                        i in self.runtime.registry.capabilities
                        and self.runtime.availability(self.runtime.registry.capabilities[i])[
                            "available"
                        ]
                        for i in ids
                    ),
                    state_machine_declared=kind == "workflow" and "state_machine" in definition,
                )
                if not registration["source_present"]:
                    diagnostics.append(
                        diagnostic(
                            "frontend_source_missing", "已加载声明，但共享组件源文件不在安装包中。"
                        )
                    )
                owner_modules = sorted(
                    {m["id"] for m in modules for cap in m["capabilities"] if cap["id"] in ids}
                )
                items[key].append(
                    {
                        **{k: value[k] for k in ("id", "version", "title", "pack")},
                        "module_ids": owner_modules,
                        "definition": definition,
                        "registration": registration,
                        "diagnostics": diagnostics,
                    }
                )
        capabilities = []
        for module in modules:
            for raw in module["capabilities"]:
                current = self.runtime.registry.capabilities.get(raw["id"])
                availability = (
                    self.runtime.availability(current)
                    if current
                    else {
                        "available": False,
                        "state": "unpublished",
                        "message": "能力尚未发布或已停用。",
                    }
                )
                value = {
                    k: deepcopy(raw[k])
                    for k in (
                        "id",
                        "version",
                        "title",
                        "description",
                        "kind",
                        "execution",
                        "presentation",
                        "input_schema",
                        "output_schema",
                    )
                    if k in raw
                }
                bindings = {
                    key: [
                        item["id"]
                        for item in group
                        if any(
                            a["capability_id"] == raw["id"]
                            for a in (
                                [a for s in item["definition"]["steps"] for a in s["actions"]]
                                if key == "workflows"
                                else item["definition"]["actions"]
                            )
                        )
                        or key == "workflows"
                        and raw["id"] in item["definition"]["provider"].values()
                    ]
                    for key, group in items.items()
                }
                capabilities.append(
                    {
                        **value,
                        "module_id": module["id"],
                        "available": availability["available"],
                        "availability": availability,
                        "bindings": bindings,
                        "diagnostics": (
                            []
                            if current
                            else [
                                diagnostic(
                                    "capability_unpublished",
                                    "声明已发现，执行注册表尚未发布该能力。",
                                    "warning",
                                )
                            ]
                        ),
                    }
                )
        result = {
            "protocol_version": "1.0",
            "generated_at": now(),
            "capabilities": capabilities,
            **items,
            "modules": [
                {k: m.get(k) for k in ("id", "name", "version", "enabled", "source")}
                for m in modules
            ],
        }
        result["summary"] = {
            "capabilities": len(capabilities),
            "workflows": len(items["workflows"]),
            "interactions": len(items["interactions"]),
            "state_machines": sum(
                x["registration"]["state_machine_declared"] for x in items["workflows"]
            ),
        }
        self.authorize(actor)
        return result

    def validate(self, actor, kind, value):
        self.authorize(actor)
        require(
            kind in ("workflow", "interaction", "state_machine"),
            "devtools_kind",
            "声明类型无效。",
            422,
        )
        require(isinstance(value, dict), "devtools_definition", "声明必须是 JSON 对象。", 422)
        diagnostics = []
        replacement = None
        try:
            encoded = bounded_json(value)
            require(
                self.runtime.redact(encoded) == encoded,
                "devtools_secret",
                "声明中不能包含已配置的服务密钥。",
                422,
            )
            if kind == "state_machine":
                validate_state_machine(value)
                matches = [
                    flow
                    for flow in self.runtime.workflows.definitions.values()
                    if flow.get("state_machine", {}).get("id") == value["id"]
                ]
                for flow in matches:
                    validate_state_machine(value, flow)
                existing = matches[0]["state_machine"] if matches else None
                if not matches:
                    diagnostics.append(
                        diagnostic(
                            "state_machine_unbound",
                            "独立状态图结构有效；尚无同编号流程可核对步骤与动作引用，请同时校验完整 workflow。",
                            "warning",
                        )
                    )
            else:
                schema = json.loads(
                    (
                        ROOT
                        / f'contracts/{"workflows" if kind == "workflow" else "interactions"}/v1/definition.schema.json'
                    ).read_text(encoding="utf-8")
                )
                self.runtime.registry.validate(
                    schema, {"flows": [value]} if kind == "workflow" else value
                )
                if kind == "workflow":
                    Workflows.validate_definition(value)
                    existing = self.runtime.workflows.definitions.get(value["id"])
                else:
                    for name in ("input_schema", "state_schema"):
                        Draft202012Validator.check_schema(value[name])
                        Registry._check_refs(value[name])
                        require(
                            value[name].get("type") == "object",
                            "component_schema",
                            "组件输入和状态必须为对象。",
                            422,
                        )

                    # Do not interpret arbitrary user-supplied regex during preview.
                    # The installation validator remains the full acceptance gate.
                    def contains_pattern(node):
                        return (
                            isinstance(node, dict)
                            and (
                                any(key in node for key in ("pattern", "patternProperties"))
                                or any(contains_pattern(v) for v in node.values())
                            )
                            or isinstance(node, list)
                            and any(contains_pattern(v) for v in node)
                        )

                    require(
                        not contains_pattern(value["state_schema"]),
                        "devtools_pattern",
                        "预览不执行状态 Schema 的正则表达式；请通过离线安装检查验证该声明。",
                        422,
                    )
                    self.runtime.registry.validate(value["state_schema"], value["initial_state"])
                    ids = [a["id"] for a in value["actions"]]
                    require(
                        len(ids) == len(set(ids)), "component_action", "组件动作标识重复。", 422
                    )
                    existing = self.runtime.interactions.definitions.get(value["id"])
                diagnostics.extend(self._reference_diagnostics(kind, value))
            replacement = {
                "registered": existing is not None,
                "incoming_version": value["version"],
                "changed": existing is not None and digest(declaration(existing)) != digest(value),
            }
            if existing:
                replacement["current_version"] = existing["version"]
                if replacement["changed"]:
                    diagnostics.append(
                        diagnostic(
                            "replacement_preview",
                            "此声明会替换同编号定义。这里仅预览，不写入、不安装，也不迁移旧实例。",
                            "warning",
                        )
                    )
        except PlatformError as exc:
            diagnostics.append(diagnostic(exc.code, exc.message))
        except (SchemaError, Unresolvable, ValueError, RecursionError):
            diagnostics.append(
                diagnostic("devtools_schema", "声明含无效 JSON Schema 或超出可安全校验的结构。")
            )
        valid = not any(item["severity"] == "error" for item in diagnostics)
        self.authorize(actor)
        identifier = value.get("id")
        identifier = (
            identifier
            if isinstance(identifier, str)
            and len(identifier) <= 120
            and self.runtime.redact(identifier) == identifier
            else None
        )
        return {
            "valid": valid,
            "kind": kind,
            "id": identifier,
            "diagnostics": diagnostics,
            "replacement": replacement,
            "preview": deepcopy(value) if valid else None,
        }

    def instances(self, actor):
        actor = self.authorize(actor)
        return self.runtime.workflows.list(actor)

    async def observe(self, actor, instance_id):
        actor = self.authorize(actor)
        with self.runtime.sessions() as db:
            _, installed = self.runtime.workflows.load(db, actor, instance_id)
            provider = self.runtime.registry.get(installed["provider"]["read_capability"], actor)
            require(
                self.runtime.availability(provider)["available"],
                "workflow_unavailable",
                "流程服务当前不可用，请稍后重试。",
                503,
            )
        # The existing service rechecks current identity, private owner, scoped
        # grants, definition version, receipt permission, and module projection.
        view = await self.runtime.workflows.get(actor, instance_id)
        projection = view["projection"]
        with self._observation_lock, self.runtime.sessions.begin() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
            require(can_manage_platform(actor), "forbidden", "调试工作台需要平台管理权限。", 403)
            item, definition = self.runtime.workflows.load(db, actor, instance_id)
            require(
                item.revision == view["revision"] and item.object_ref == view["object_ref"],
                "revision_conflict",
                "实例刚刚变化，请重新观察。",
                409,
            )
            fsm = deepcopy(definition.get("state_machine"))
            state = projection["state"] if projection else None
            allowed = sorted(set(projection["allowed_actions"])) if projection else []
            error = view.get("error")
            # Module error messages may contain input or credentials. Record only
            # a generic host code, not even an arbitrary remote-provided code.
            error_code = "workflow_execution_error" if error else None
            observed = {
                "cursor": view["cursor"],
                "revision": view["revision"],
                "business_state": state,
                "business_revision": projection["revision"] if projection else None,
                "allowed_actions": allowed,
                "execution_status": view["status"],
                "error_code": error_code,
            }
            object_key = digest(view["object_ref"])
            rows = (
                db.query(WorkflowObservation)
                .filter_by(instance_id=instance_id, owner_id=actor["id"])
                .order_by(WorkflowObservation.observed_at.desc(), WorkflowObservation.id.desc())
                .all()
            )
            fingerprint = digest({"object_key": object_key, **observed})
            if not rows or rows[0].fingerprint != fingerprint:
                row = WorkflowObservation(
                    instance_id=instance_id,
                    owner_id=actor["id"],
                    object_key=object_key,
                    fingerprint=fingerprint,
                    observed=observed,
                )
                db.add(row)
                db.flush()
                rows.insert(0, row)
            for obsolete in rows[MAX_HISTORY:]:
                db.delete(obsolete)
            history = [
                {"id": row.id, "observed_at": row.observed_at, **deepcopy(row.observed)}
                for row in reversed(rows[:MAX_HISTORY])
                if row.object_key == object_key
            ]
        diagnostics = []
        known_state = (
            None if fsm is None or state is None else state in {s["id"] for s in fsm["states"]}
        )
        if fsm is None:
            diagnostics.append(
                diagnostic(
                    "state_machine_absent",
                    "模块未声明业务状态机，不能从步骤导航推断状态图。",
                    "info",
                )
            )
        elif known_state is False:
            diagnostics.append(
                diagnostic(
                    "state_machine_unknown_state",
                    "服务返回了未在状态机中声明的真实状态。请检查模块实现与声明版本。",
                    "warning",
                )
            )
        if projection is None:
            diagnostics.append(
                diagnostic(
                    "business_object_absent", "尚未关联正式业务对象；当前只有个人步骤导航。", "info"
                )
            )
        return {
            "instance_id": instance_id,
            "flow_id": view["flow_id"],
            "flow_version": view["flow_version"],
            "title": view["title"],
            "observed_at": now(),
            "cursor": view["cursor"],
            "revision": view["revision"],
            "object_ref": view["object_ref"],
            "business": {
                "state": state,
                "revision": projection["revision"] if projection else None,
                "allowed_actions": allowed,
                "known_state": known_state,
            },
            "execution": {
                "status": view["status"],
                "run_id": view["run_id"],
                "error_code": error_code,
            },
            "state_machine": fsm,
            "history": history,
            "diagnostics": diagnostics,
            "history_note": HISTORY_NOTE,
        }
