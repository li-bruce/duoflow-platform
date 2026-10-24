"""Pure atomic-registration checks shared by the platform and the module kit.

Definitions describe behavior; they never execute it. Backend schemas come from
the real capability manifest. Host checks compare frontend events with installed
interaction/workflow definitions, rather than trusting a second function list.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import require
from .registration_contract import check_schema

ROOT = Path(__file__).resolve().parents[1]


def _unique(items, key, message):
    """Index declared identities once and reject accidental overwrites."""
    result = {item[key]: item for item in items}
    require(len(result) == len(items), "module_graph_duplicate", message, 422)
    return result


def _same_schema(actual, expected, label):
    """Require structural equality; no implicit narrowing or coercion is assumed."""
    require(actual == expected, "module_graph_schema", label + "与实际接口不一致。", 422)


def validate_module_graph(document, manifest):
    """Validate complete identities, schemas, mappings and state references.

    Return an isolated copy. Validation grants no permissions and imports no
    supplier source; installed-host and callable checks are separate boundaries.
    """
    schema = json.loads(
        (ROOT / "contracts/module-graph/v1/definition.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(document)
    module = manifest.get("module", manifest)
    require(
        (document["module_id"], document["module_version"]) == (module["id"], module["version"]),
        "module_graph_identity",
        "业务图与模块身份或版本不一致。",
        422,
    )
    capabilities = _unique(manifest["capabilities"], "id", "能力编号重复。")
    atoms = _unique(document["atoms"], "id", "原子组件编号重复。")
    nodes = _unique(document["nodes"], "id", "业务节点编号重复。")
    _unique(document["edges"], "id", "关系编号重复。")
    machines = _unique(document["state_machines"], "id", "状态机编号重复。")
    prefix = document["module_id"] + "."
    require(
        all(value.startswith(prefix) for value in [*atoms, *nodes, *machines]),
        "module_graph_namespace",
        "原子、业务节点和状态机应使用模块命名空间。",
        422,
    )
    bound_capabilities, host_keys = [], []
    for atom in atoms.values():
        if atom["kind"] == "backend":
            binding = atom["capability"]
            require(
                binding["id"] in capabilities
                and capabilities[binding["id"]]["version"] == binding["version"],
                "module_graph_capability",
                "后端原子引用了未知能力或错误版本。",
                422,
            )
            bound_capabilities.append(binding["id"])
            continue
        host = atom["host"]
        require(
            (host["kind"] == "workflow_step") == ("step_id" in host),
            "module_graph_host",
            "流程步骤必须明确 step_id；单组件不使用步骤字段。",
            422,
        )
        require(
            host["id"].startswith(prefix), "module_graph_namespace", "组件宿主应属于本模块。", 422
        )
        host_keys.append((host["kind"], host["id"], host.get("step_id")))
        for field in ("input_schema", "output_schema", "state_schema"):
            check_schema(atom[field])
            require(
                atom[field].get("type") == "object",
                "module_graph_schema",
                "组件输入、输出与状态须为对象。",
                422,
            )
        Draft202012Validator(atom["state_schema"]).validate(atom["initial_state"])
        _unique(atom["events"], "id", "组件事件编号重复。")
        for event in atom["events"]:
            check_schema(event["input_schema"])
            check_schema(event["output_schema"])
            binding = event.get("capability")
            if binding:
                require(
                    binding["id"] in capabilities
                    and capabilities[binding["id"]]["version"] == binding["version"],
                    "module_graph_event",
                    "交互事件引用了未知能力或错误版本。",
                    422,
                )
                _same_schema(
                    event["output_schema"], capabilities[binding["id"]]["output_schema"], "事件输出"
                )
    require(
        len(bound_capabilities) == len(set(bound_capabilities))
        and set(bound_capabilities) == set(capabilities),
        "module_graph_coverage",
        "每项公开后端能力必须恰有一个原子定义。",
        422,
    )
    require(
        len(host_keys) == len(set(host_keys)),
        "module_graph_duplicate",
        "同一组件宿主重复登记。",
        422,
    )
    mapping_keys, mapped_atoms = set(), set()
    for mapping in document["mappings"]:
        atom = atoms.get(mapping["atom_id"])
        require(
            atom is not None and mapping["node_id"] in nodes,
            "module_graph_mapping",
            "映射的组件或业务节点不存在。",
            422,
        )
        event_id = mapping.get("event_id")
        require(
            event_id is None
            or (atom["kind"] == "frontend" and event_id in {e["id"] for e in atom["events"]}),
            "module_graph_mapping",
            "映射引用了未登记的交互事件。",
            422,
        )
        key = (mapping["atom_id"], mapping["node_id"], mapping["role"], event_id)
        require(key not in mapping_keys, "module_graph_duplicate", "业务映射重复。", 422)
        mapping_keys.add(key)
        mapped_atoms.add(atom["id"])
    require(
        mapped_atoms == set(atoms), "module_graph_coverage", "每个原子组件都需要业务节点映射。", 422
    )
    for edge in document["edges"]:
        require(
            edge["from"] in nodes and edge["to"] in nodes,
            "module_graph_edge",
            "关系端点不存在。",
            422,
        )
        if edge["kind"] == "transition":
            require(
                nodes[edge["from"]]["kind"] == nodes[edge["to"]]["kind"] == "state",
                "module_graph_edge",
                "状态转换的两端必须是状态节点。",
                422,
            )
    for machine in machines.values():
        states = _unique(machine["states"], "id", "状态值重复。")
        _unique(machine["transitions"], "id", "状态转换编号重复。")
        require(machine["initial"] in states, "module_graph_state", "初始状态未声明。", 422)
        require(
            all(nodes.get(s["node_id"], {}).get("kind") == "state" for s in states.values()),
            "module_graph_state",
            "状态值必须映射到状态节点。",
            422,
        )
        require(
            all(t["from"] in states and t["to"] in states for t in machine["transitions"]),
            "module_graph_state",
            "状态转换引用了未知状态。",
            422,
        )
        if machine["scope"] == "component":
            require(
                atoms.get(machine["owner_id"], {}).get("kind") == "frontend",
                "module_graph_state",
                "组件状态机需要前端原子定义。",
                422,
            )
        if machine["scope"] == "execution":
            require(
                machine["owner_id"] in atoms, "module_graph_state", "调用状态机需要原子定义。", 422
            )
    return deepcopy(document)


def validate_graph_hosts(document, manifest, *, interactions=(), workflows=()):
    """Compare graph events against actual, already validated shared hosts.

    Workflow inputs describe the visible object projection; event input schemas
    describe action commands. UI drafts remain distinct from formal object state.
    """
    graph = validate_module_graph(document, manifest)
    capabilities = {c["id"]: c for c in manifest["capabilities"]}
    interaction_map = {value["id"]: value for value in interactions}
    workflow_map = {value["id"]: value for value in workflows}
    expected_hosts = {("interaction", value["id"], None) for value in interactions}
    expected_hosts.update(
        ("workflow_step", value["id"], step["id"]) for value in workflows for step in value["steps"]
    )
    actual_hosts = {
        (a["host"]["kind"], a["host"]["id"], a["host"].get("step_id"))
        for a in graph["atoms"]
        if a["kind"] == "frontend"
    }
    require(
        actual_hosts == expected_hosts,
        "module_graph_coverage",
        "原子登记必须覆盖全部共享组件和流程步骤。",
        422,
    )
    for atom in graph["atoms"]:
        if atom["kind"] != "frontend":
            continue
        binding = atom["host"]
        definitions = interaction_map if binding["kind"] == "interaction" else workflow_map
        host = definitions.get(binding["id"])
        require(
            host is not None and host["version"] == binding["version"],
            "module_graph_host",
            "组件宿主未安装或版本不一致。",
            422,
        )
        if binding["kind"] == "interaction":
            for field in ("input_schema", "state_schema", "initial_state"):
                _same_schema(atom[field], host[field], field)
            actions = host["actions"]
        else:
            step = next((s for s in host["steps"] if s["id"] == binding["step_id"]), None)
            require(step is not None, "module_graph_host", "流程步骤未安装。", 422)
            actions = step["actions"]
        declared = {event["id"]: event for event in atom["events"] if "capability" in event}
        require(
            set(declared) == {action["id"] for action in actions},
            "module_graph_host",
            "组件登记必须覆盖全部实际业务动作。",
            422,
        )
        for action in actions:
            event = declared[action["id"]]
            require(
                event["capability"]
                == {"id": action["capability_id"], "version": action["capability_version"]},
                "module_graph_host",
                "组件事件与实际动作绑定不一致。",
                422,
            )
            actual_input = (
                action["command_schema"]
                if binding["kind"] == "workflow_step"
                else capabilities[action["capability_id"]]["input_schema"]
            )
            _same_schema(event["input_schema"], actual_input, "事件输入")
    expected_machines = {value["id"] for value in workflows if value.get("state_machine")}
    actual_machines = [m["owner_id"] for m in graph["state_machines"] if m["scope"] == "business"]
    require(
        set(actual_machines) == expected_machines
        and len(actual_machines) == len(set(actual_machines)),
        "module_graph_coverage",
        "每个正式业务流程必须有对应的状态机映射。",
        422,
    )
    for machine in graph["state_machines"]:
        if machine["scope"] != "business":
            continue
        owner = workflow_map.get(machine["owner_id"])
        require(
            owner is not None and "state_machine" in owner,
            "module_graph_state",
            "正式状态机缺少实际流程声明。",
            422,
        )
        actual = owner["state_machine"]
        require(
            machine["initial"] == actual["initial"]
            and {s["id"] for s in machine["states"]} == {s["id"] for s in actual["states"]},
            "module_graph_state",
            "正式状态机与模块流程声明不一致。",
            422,
        )
        expected = {(t["id"], t["from"], t["to"], t["action_id"]) for t in actual["transitions"]}
        require(
            {(t["id"], t["from"], t["to"], t["event"]) for t in machine["transitions"]} == expected,
            "module_graph_state",
            "正式状态转换与模块声明不一致。",
            422,
        )
    return graph


def resolved_atoms(document, manifest):
    """Expose backend I/O from one authority and preserve explicit node mappings."""
    graph = validate_module_graph(document, manifest)
    capabilities = {value["id"]: value for value in manifest["capabilities"]}
    for atom in graph["atoms"]:
        atom["mappings"] = [m for m in graph["mappings"] if m["atom_id"] == atom["id"]]
        if atom["kind"] == "backend":
            capability = capabilities[atom["capability"]["id"]]
            atom["input_schema"] = deepcopy(capability["input_schema"])
            atom["output_schema"] = deepcopy(capability["output_schema"])
    return graph["atoms"]
