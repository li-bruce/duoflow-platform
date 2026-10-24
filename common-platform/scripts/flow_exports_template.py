"""Create editable public entries from real capabilities and shared workflows."""

from app.flow_exports import flow_capabilities


def exports_template(manifest, registration, graph, *, workflows=()):
    """Declare callable boundaries; authors can remove entries they keep private."""
    module = manifest.get("module", manifest)
    flows = []
    hosts = {w["id"]: w for w in workflows}
    permissions = {
        m["capability_id"]: m["permission"]
        for m in (registration["permissions"]["definition"] or {}).get("command_mappings", [])
    }
    for cap in manifest["capabilities"]:
        flows.append(
            {
                "id": cap["id"] + ".public",
                "version": cap["version"],
                "title": cap["title"],
                "description": cap["description"],
                "entry": {"kind": "capability", "id": cap["id"], "version": cap["version"]},
            }
        )
    for workflow in workflows:
        terminal = [s["id"] for s in workflow["state_machine"]["states"] if s.get("terminal")]
        if not terminal:
            raise ValueError("公开流程须明确实际完成状态。")
        flows.append(
            {
                "id": workflow["id"] + ".public",
                "version": workflow["version"],
                "title": workflow["title"],
                "description": workflow["description"],
                "entry": {
                    "kind": "workflow",
                    "id": workflow["id"],
                    "version": workflow["version"],
                    "completed_states": terminal,
                },
            }
        )
    for flow in flows:
        dependencies, start = flow_capabilities(flow, hosts)
        atoms = {
            a["id"]
            for a in graph["atoms"]
            if a["kind"] == "backend" and a["capability"]["id"] in dependencies
        }
        flow["node_ids"] = sorted(
            {m["node_id"] for m in graph["mappings"] if m["atom_id"] in atoms}
        )
        flow["required_permissions"] = [permissions[start]] if start in permissions else []
        flow["data_access"] = []
        for obj in registration["data_objects"]:
            operations = sorted(op for op, cap in obj["operations"].items() if cap in dependencies)
            if operations:
                flow["data_access"].append({"object_id": obj["id"], "operations": operations})
    return {
        "exports_version": "1.0",
        "module_id": module["id"],
        "module_version": module["version"],
        "flows": flows,
    }
