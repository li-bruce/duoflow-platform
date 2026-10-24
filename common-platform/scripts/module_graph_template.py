"""Build an editable atom registry from the module's actual host contracts.

Only declared interfaces and state transitions are projected. Developers add
internal nodes and observations to this starting document; the function never
claims to infer private business rules from source names.
"""

from copy import deepcopy


def graph_template(manifest, *, interactions=(), workflows=(), runtime="node"):
    module = manifest.get("module", manifest)
    prefix = module["id"]
    capabilities = {cap["id"]: cap for cap in manifest["capabilities"]}
    graph = {
        "graph_version": "1.0",
        "module_id": prefix,
        "module_version": module["version"],
        "version": "1.0.0",
        "atoms": [],
        "nodes": [],
        "mappings": [],
        "edges": [],
        "state_machines": [],
    }
    empty = {"type": "object", "properties": {}, "additionalProperties": False}

    def node(identifier, title, kind="business", description=None):
        if not any(n["id"] == identifier for n in graph["nodes"]):
            graph["nodes"].append(
                {
                    "id": identifier,
                    "title": title,
                    "kind": kind,
                    "description": description or title,
                }
            )

    def mapping(atom, target, role="handles", event_id=None):
        value = {"atom_id": atom, "node_id": target, "role": role}
        if event_id:
            value["event_id"] = event_id
        graph["mappings"].append(value)

    for cap in capabilities.values():
        atom_id, node_id = cap["id"] + ".backend", cap["id"] + ".business"
        source = (
            {
                "path": "backend/handlers.py",
                "symbol": cap.get("handler", "handlers:analyze").split(":")[-1],
            }
            if runtime == "python"
            else {"path": "backend/business.mjs", "symbol": "createBusiness.invoke"}
        )
        graph["atoms"].append(
            {
                "id": atom_id,
                "kind": "backend",
                "title": cap["title"],
                "description": cap["description"],
                "source": source,
                "capability": {"id": cap["id"], "version": cap["version"]},
            }
        )
        node(node_id, cap["title"])
        mapping(atom_id, node_id)

    def frontend(
        identifier, title, description, host, source, inputs, output, state, initial, actions
    ):
        events = []
        for action in actions:
            cap = capabilities[action["capability_id"]]
            events.append(
                {
                    "id": action["id"],
                    "title": action["title"],
                    "input_schema": deepcopy(action.get("command_schema", cap["input_schema"])),
                    "output_schema": deepcopy(cap["output_schema"]),
                    "effect": "调用已登记的后端功能并显示返回结果。",
                    "state_effect": "执行中显示等待；成功后读取实际结果，失败时保留可修改内容。",
                    "capability": {"id": cap["id"], "version": cap["version"]},
                }
            )
            mapping(identifier, cap["id"] + ".business", event_id=action["id"])
        events.append(
            {
                "id": "draft.change",
                "title": "修改填写内容",
                "input_schema": deepcopy(state),
                "output_schema": deepcopy(state),
                "effect": "更新当前使用者的填写内容。",
                "state_effect": "个人草稿变化；正式业务对象保持原状态。",
            }
        )
        graph["atoms"].append(
            {
                "id": identifier,
                "kind": "frontend",
                "title": title,
                "description": description,
                "source": source,
                "host": host,
                "input_schema": deepcopy(inputs),
                "output_schema": deepcopy(output),
                "state_schema": deepcopy(state),
                "initial_state": deepcopy(initial),
                "events": events,
            }
        )
        view_node = identifier + ".view"
        node(view_node, title)
        mapping(identifier, view_node, "shows")

    for definition in interactions:
        caps = [capabilities[a["capability_id"]] for a in definition["actions"]]
        output = (
            caps[0]["output_schema"]
            if len(caps) == 1
            else {"type": "object", "oneOf": [c["output_schema"] for c in caps]}
        )
        identifier = definition["id"] + ".frontend"
        frontend(
            identifier,
            definition["title"],
            definition["description"],
            {"kind": "interaction", "id": definition["id"], "version": definition["version"]},
            {"path": "frontend/Component.tsx", "symbol": "default"},
            definition["input_schema"],
            output,
            definition["state_schema"],
            definition["initial_state"],
            definition["actions"],
        )
        # These values are invocation outcomes, not invented domain states.
        states = ["idle", "running", "succeeded", "failed"]
        for state in states:
            node(
                identifier + "." + state,
                {
                    "idle": "待操作",
                    "running": "执行中",
                    "succeeded": "执行成功",
                    "failed": "执行失败",
                }[state],
                "state",
            )
        graph["state_machines"].append(
            {
                "id": identifier + ".execution",
                "scope": "execution",
                "owner_id": identifier,
                "initial": "idle",
                "states": [{"id": s, "node_id": identifier + "." + s} for s in states],
                "transitions": [
                    {"id": "start", "from": "idle", "to": "running", "event": "action.start"},
                    {
                        "id": "success",
                        "from": "running",
                        "to": "succeeded",
                        "event": "action.success",
                    },
                    {"id": "failure", "from": "running", "to": "failed", "event": "action.failure"},
                    {"id": "again", "from": "succeeded", "to": "running", "event": "action.start"},
                    {"id": "retry", "from": "failed", "to": "running", "event": "action.start"},
                ],
            }
        )

    for flow in workflows:
        read_output = capabilities[flow["provider"]["read_capability"]]["output_schema"]
        for step in flow["steps"]:
            fields = {}
            for action in step["actions"]:
                fields.update(deepcopy(action["command_schema"].get("properties", {})))
            state = {"type": "object", "properties": fields, "additionalProperties": False}
            frontend(
                flow["id"] + "." + step["id"] + ".frontend",
                step["title"],
                step["description"],
                {
                    "kind": "workflow_step",
                    "id": flow["id"],
                    "version": flow["version"],
                    "step_id": step["id"],
                },
                {"path": "frontend/steps.tsx", "symbol": step["component"]},
                read_output,
                read_output,
                state,
                {},
                step["actions"],
            )
        actual = flow.get("state_machine")
        if actual:
            for state in actual["states"]:
                node(flow["id"] + ".state." + state["id"], state["title"], "state")
            machine = {
                "id": actual["id"],
                "scope": "business",
                "owner_id": flow["id"],
                "initial": actual["initial"],
                "states": [
                    {"id": s["id"], "node_id": flow["id"] + ".state." + s["id"]}
                    for s in actual["states"]
                ],
                "transitions": [
                    {"id": t["id"], "from": t["from"], "to": t["to"], "event": t["action_id"]}
                    for t in actual["transitions"]
                ],
            }
            graph["state_machines"].append(machine)
            for transition in actual["transitions"]:
                graph["edges"].append(
                    {
                        "id": flow["id"] + "." + transition["id"],
                        "from": flow["id"] + ".state." + transition["from"],
                        "to": flow["id"] + ".state." + transition["to"],
                        "kind": "transition",
                        "title": transition["title"],
                    }
                )
        else:
            raise ValueError(
                "A workflow requires its actual business-state declaration before graph registration"
            )
    return graph
