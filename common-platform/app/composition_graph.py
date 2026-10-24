"""Join declared business/state/data nodes with authorized live runtime evidence.

Graph IDs are namespaced by composition step because the same module may be used
twice. Observations color activity only; a fresh object projection supplies the
formal business state. Missing or stale observations remain visible as such.
"""

from copy import deepcopy

from .db import Invocation, WorkflowInstance
from .errors import PlatformError


class CompositionGraph:
    def __init__(self, service):
        self.service, self.runtime = service, service.runtime

    def machines(self, graph, flow):
        """Select state machines for the actual exported host or declared data objects."""
        object_types = {item["id"] for item in flow["data_objects"]}
        owners = {flow["entry"]["id"], *object_types}
        owners.update(
            identifier
            for identifier, definition in self.runtime.workflows.definitions.items()
            if definition["provider"]["object_type"] in object_types
        )
        return [
            m
            for m in graph["state_machines"]
            if m["scope"] == "business" and m["owner_id"] in owners
        ]

    def matches_object(self, machine, projection):
        """A similarly named state in another object's lifecycle is not current."""
        if not projection:
            return False
        owner = machine["owner_id"]
        workflow = self.runtime.workflows.definitions.get(owner)
        object_type = projection["object_ref"]["object_type"]
        return object_type == (workflow["provider"]["object_type"] if workflow else owner)

    async def runtime_evidence(self, actor, step, pin, projection):
        """Read fresh object state and activity, keeping their authority separate."""
        graph = pin["graph"]
        instance = None
        invocation_id = step["invocation_id"]
        if step["instance_id"]:
            instance = await self.runtime.workflows.get(actor, step["instance_id"])
            projection = instance.get("projection")
            with self.runtime.sessions() as db:
                host = db.get(WorkflowInstance, step["instance_id"])
                invocation_id = (host.last_result or {}).get(
                    "invocation_id"
                ) or host.completed_invocation_id
        activity = {}
        observed = {"step_id": step["step_id"], "freshness": "empty", "gaps": [], "dropped": 0}
        if invocation_id:
            try:
                trace = await self.runtime.observations.read(actor, invocation_id)
                observed.update(
                    {key: trace.get(key) for key in ("freshness", "gaps", "dropped", "received_at")}
                )
                observed["request_id"] = invocation_id
                for event in trace["events"]:
                    if event["phase"] != "state":
                        activity[event["atom_id"]] = event["phase"]
            except PlatformError as failure:
                observed.update(freshness="unavailable", message=failure.message)
            if not pin["service_id"]:
                # Local invocations have a durable receipt even without a remote callback.
                with self.runtime.sessions() as db:
                    invocation = db.get(Invocation, invocation_id)
                    self.runtime.check_invocation_authorization(invocation, actor)
                    for atom in graph["atoms"]:
                        if atom.get("capability", {}).get("id") == invocation.capability_id:
                            activity[atom["id"]] = (
                                "succeeded" if invocation.status == "completed" else "running"
                            )
                observed.update(freshness="receipt", message="本地调用记录")
        return instance, projection, activity, observed

    @staticmethod
    def connect_inputs(definition, prefix, root, nodes, edge):
        """Draw explicit field transfers using namespaced input/output nodes."""
        for field, binding in definition["bindings"].items():
            target = prefix + "input:" + field
            nodes.append(
                {
                    "id": target,
                    "title": field,
                    "kind": "data",
                    "step_id": definition["id"],
                    "direction": "input",
                }
            )
            edge(target, root, "data", "作为本步输入")
            if binding["source"] == "step":
                source = binding["step_id"] + "::output:" + "/".join(binding["path"])
                if not any(node["id"] == source for node in nodes):
                    nodes.append(
                        {
                            "id": source,
                            "title": " · ".join(binding["path"]),
                            "kind": "data",
                            "step_id": binding["step_id"],
                            "direction": "output",
                        }
                    )
                    edge(binding["step_id"] + "::entry", source, "data", "读取来源结果")
                edge(source, target, "data", "传递指定字段")

    async def read(self, actor, run_id):
        """Join the saved plan with current authorized states, calls and visible hosts."""
        snapshot = self.service.load(actor, run_id)
        public = await self.service.read(actor, run_id)
        nodes, edges, groups, observations = [], [], [], []

        def edge(source, target, kind, title):
            edges.append(
                {
                    "id": f"edge-{len(edges)}",
                    "from": source,
                    "to": target,
                    "kind": kind,
                    "title": title,
                }
            )

        for index, step in enumerate(snapshot["steps"]):
            definition = snapshot["document"]["steps"][index]
            pin = snapshot["pins"][step["step_id"]]
            prefix = step["step_id"] + "::"
            root = prefix + "entry"
            nodes.append(
                {
                    "id": root,
                    "title": definition["title"],
                    "kind": "entry",
                    "step_id": step["step_id"],
                    "activity": step["status"],
                }
            )
            groups.append(
                {
                    "id": step["step_id"],
                    "title": definition["title"],
                    "module_id": pin["module_id"],
                    "status": step["status"],
                }
            )
            if index:
                edge(
                    snapshot["steps"][index - 1]["step_id"] + "::entry",
                    root,
                    "sequence",
                    "完成后继续",
                )
            if not public["content_available"]:
                continue
            graph, flow = pin["graph"], pin["flow"]
            machines = self.machines(graph, flow)
            included = set(flow["node_ids"]) | {s["node_id"] for m in machines for s in m["states"]}
            atoms = {m["atom_id"] for m in graph["mappings"] if m["node_id"] in included}
            included |= {m["node_id"] for m in graph["mappings"] if m["atom_id"] in atoms}
            projection = (public["steps"][index].get("output") or {}).get("projection")
            instance, projection, activity, observed = await self.runtime_evidence(
                actor, step, pin, projection
            )
            observations.append(observed)
            state_nodes = {
                state["node_id"]: state["id"]
                for machine in machines
                if self.matches_object(machine, projection)
                for state in machine["states"]
            }
            for node in graph["nodes"]:
                if node["id"] not in included:
                    continue
                related = [
                    mapping["atom_id"]
                    for mapping in graph["mappings"]
                    if mapping["node_id"] == node["id"]
                ]
                nodes.append(
                    {
                        **deepcopy(node),
                        "id": prefix + node["id"],
                        "step_id": step["step_id"],
                        "current_state": bool(
                            projection and state_nodes.get(node["id"]) == projection["state"]
                        ),
                        "business_revision": projection.get("revision") if projection else None,
                        "activity": next((activity[a] for a in related if a in activity), None),
                        "atom_ids": related,
                    }
                )
                if node["kind"] == "business":
                    edge(root, prefix + node["id"], "contains", "公开业务")
            for relation in graph["edges"]:
                if relation["from"] in included and relation["to"] in included:
                    edge(
                        prefix + relation["from"],
                        prefix + relation["to"],
                        relation["kind"],
                        relation["title"],
                    )
            self.connect_inputs(definition, prefix, root, nodes, edge)
            if instance:
                atomic = await self.runtime.atomic_instances.read(actor, "workflow", instance["id"])
                groups[-1]["instances"] = atomic.get("instances", [])
        if public["content_available"]:
            self.service.check_pins(actor, snapshot)
        return {
            "run_id": run_id,
            "revision": public["revision"],
            "content_available": public["content_available"],
            "groups": groups,
            "nodes": nodes,
            "edges": edges,
            "observations": observations,
            "error": public["error"],
        }
