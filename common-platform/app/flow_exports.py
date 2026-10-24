"""Resolve deliberate public business entries against their real interfaces.

An export names one callable capability or one shared workflow with an explicit
completion boundary. It never extracts arbitrary source code or grants access.
The module remains responsible for object facts and formal state transitions.
"""

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import require
from .module_graph import validate_module_graph

ROOT = Path(__file__).resolve().parents[1]


def validate_export_references(document, manifest, registration, graph):
    """Check remote declarations before actual installed-host validation.

    Workflow input and action permissions are checked against real hosts in
    validate_flow_exports; this pass cannot approve publication by itself.
    """
    schema = json.loads(
        (ROOT / "contracts/flow-exports/v1/definition.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(document)
    module = manifest.get("module", manifest)
    require(
        (document["module_id"], document["module_version"]) == (module["id"], module["version"]),
        "flow_export_identity",
        "公开业务流身份不一致。",
        422,
    )
    caps = {c["id"]: c for c in manifest["capabilities"]}
    hosts = {h["id"]: h for h in registration["host_requirements"]["workflows"]}
    machines = {m["owner_id"]: m for m in graph["state_machines"] if m["scope"] == "business"}
    nodes = {n["id"] for n in graph["nodes"]}
    ids = [f["id"] for f in document["flows"]]
    require(len(ids) == len(set(ids)), "flow_export_duplicate", "公开业务流编号重复。", 422)
    for flow in document["flows"]:
        entry = flow["entry"]
        target = caps.get(entry["id"]) if entry["kind"] == "capability" else hosts.get(entry["id"])
        require(
            flow["id"].startswith(module["id"] + ".")
            and target is not None
            and target["version"] == entry["version"]
            and set(flow["node_ids"]) <= nodes,
            "flow_export_reference",
            "公开业务流引用不一致。",
            422,
        )
        if entry["kind"] == "workflow":
            machine = machines.get(entry["id"])
            require(
                machine is not None
                and set(entry["completed_states"]) <= {s["id"] for s in machine["states"]},
                "flow_export_state",
                "公开流程的完成状态不一致。",
                422,
            )


def flow_capabilities(flow, workflows):
    """Return actual entry and action dependencies, preserving workflow authority."""
    entry = flow["entry"]
    if entry["kind"] == "capability":
        return {entry["id"]}, entry["id"]
    workflow = workflows.get(entry["id"])
    require(workflow is not None, "flow_export_host", "公开流程没有对应的共享流程。", 422)
    read_id = workflow["provider"]["read_capability"]
    return {
        read_id,
        *(a["capability_id"] for s in workflow["steps"] for a in s["actions"]),
    }, read_id


def validate_flow_exports(document, manifest, registration, graph, *, workflows=()):
    """Check public boundaries, permission requirements and data-operation links."""
    schema = json.loads(
        (ROOT / "contracts/flow-exports/v1/definition.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(document)
    validate_module_graph(graph, manifest)
    module = manifest.get("module", manifest)
    require(
        (document["module_id"], document["module_version"]) == (module["id"], module["version"]),
        "flow_export_identity",
        "业务流与模块版本不一致。",
        422,
    )
    caps = {c["id"]: c for c in manifest["capabilities"]}
    hosts = {w["id"]: w for w in workflows}
    nodes = {n["id"] for n in graph["nodes"]}
    cap_nodes = {
        a["capability"]["id"]: {m["node_id"] for m in graph["mappings"] if m["atom_id"] == a["id"]}
        for a in graph["atoms"]
        if a["kind"] == "backend"
    }
    access = registration["permissions"]
    mappings = {
        m["capability_id"]: m["permission"]
        for m in (access["definition"] or {}).get("command_mappings", [])
    }
    objects = {o["id"]: o for o in registration["data_objects"]}
    ids = [f["id"] for f in document["flows"]]
    require(len(ids) == len(set(ids)), "flow_export_duplicate", "公开业务流编号重复。", 422)
    for flow in document["flows"]:
        require(
            flow["id"].startswith(document["module_id"] + "."),
            "flow_export_namespace",
            "业务流必须使用模块命名空间。",
            422,
        )
        entry = flow["entry"]
        target = caps.get(entry["id"]) if entry["kind"] == "capability" else hosts.get(entry["id"])
        require(
            target is not None and target["version"] == entry["version"],
            "flow_export_entry",
            "公开入口不存在或版本不匹配。",
            422,
        )
        dependencies, start = flow_capabilities(flow, hosts)
        require(
            dependencies <= caps.keys(), "flow_export_capability", "业务流引用了未知能力。", 422
        )
        required = {mappings[start]} if start in mappings else set()
        require(
            set(flow["required_permissions"]) == required,
            "flow_export_permissions",
            "入口权限必须与实际调用的权限一致。",
            422,
        )
        public_nodes = set(flow["node_ids"])
        require(
            public_nodes <= nodes and bool(public_nodes & cap_nodes[start]),
            "flow_export_nodes",
            "业务流需要关联实际入口的业务节点。",
            422,
        )
        if entry["kind"] == "workflow":
            machine = target.get("state_machine")
            provider = target["provider"]
            obj = objects.get(provider["object_type"])
            require(
                obj is not None
                and obj["operations"].get("read") == start
                and caps[start]["kind"] == "query",
                "flow_export_provider",
                "共享流程需要已登记的真实对象读取能力。",
                422,
            )
            expected_output = {
                "type": "object",
                "properties": {"projection": obj["schema"]},
                "required": ["projection"],
                "additionalProperties": False,
            }
            require(
                caps[start]["output_schema"] == expected_output,
                "flow_export_output",
                "共享流程输出应与对象投影读取接口一致。",
                422,
            )
            require(machine is not None, "flow_export_state", "公开流程需要正式业务状态图。", 422)
            require(
                set(entry["completed_states"]) <= {s["id"] for s in machine["states"]},
                "flow_export_state",
                "完成条件引用了未知业务状态。",
                422,
            )
        declared_access = {}
        for item in flow["data_access"]:
            object_id = item["object_id"]
            require(
                object_id in objects and object_id not in declared_access,
                "flow_export_data",
                "数据对象未声明或重复。",
                422,
            )
            operations = set(item["operations"])
            require(
                operations <= objects[object_id]["operations"].keys(),
                "flow_export_data",
                "业务流引用了未公开的数据操作。",
                422,
            )
            declared_access[object_id] = operations
        for object_id, obj in objects.items():
            actual = {
                op for op, capability in obj["operations"].items() if capability in dependencies
            }
            require(
                actual <= declared_access.get(object_id, set()),
                "flow_export_data",
                "公开业务流遗漏了实际使用的数据操作。",
                422,
            )
    return deepcopy(document)


def resolved_flows(document, manifest, registration, graph, *, workflows=()):
    """Expose I/O and role suggestions from existing contracts, without copying grants."""
    checked = validate_flow_exports(document, manifest, registration, graph, workflows=workflows)
    caps = {c["id"]: c for c in manifest["capabilities"]}
    hosts = {w["id"]: w for w in workflows}
    access = registration["permissions"]["definition"] or {}
    permissions = {m["capability_id"]: m["permission"] for m in access.get("command_mappings", [])}
    for flow in checked["flows"]:
        entry = flow["entry"]
        dependencies, start = flow_capabilities(flow, hosts)
        if entry["kind"] == "capability":
            flow["input_schema"] = deepcopy(caps[start]["input_schema"])
            flow["output_schema"] = deepcopy(caps[start]["output_schema"])
        else:
            # Selecting an existing object is optional; creation happens in the
            # same shared workflow component used by the standalone module.
            flow["input_schema"] = {
                "type": "object",
                "properties": {"object_id": {"type": "string", "minLength": 1, "maxLength": 100}},
                "additionalProperties": False,
            }
            flow["output_schema"] = deepcopy(caps[start]["output_schema"])
            flow["state_machine"] = deepcopy(hosts[entry["id"]]["state_machine"])
        flow["capabilities"] = [
            {
                "id": key,
                "version": caps[key]["version"],
                "kind": caps[key]["kind"],
                "permission": permissions.get(key),
                "roles": deepcopy(caps[key]["roles"]),
            }
            for key in sorted(dependencies)
        ]
        flow["role_templates"] = deepcopy(
            [
                role
                for role in access.get("role_templates", [])
                if set(role["permissions"]) & {permissions.get(key) for key in dependencies}
            ]
        )
        flow["data_objects"] = deepcopy(
            [
                obj
                for obj in registration["data_objects"]
                if obj["id"] in {item["object_id"] for item in flow["data_access"]}
            ]
        )
    return checked["flows"]
