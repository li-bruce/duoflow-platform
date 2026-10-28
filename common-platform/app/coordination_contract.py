"""Pure, portable registration checks for optional business coordination 1.0.

Declarations describe what a module can send and receive. Actual scope-specific
subscriptions and publication decisions are configured separately by the host.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
EVENT_KINDS = (
    "state.changed", "publication.requested", "resource.published", "resource.withdrawn"
)


def validate_coordination(document):
    """Validate an optional declaration against this module's objects and graph.

    This helper is intentionally free of runtime, database and access imports so
    the independent Module Kit can use exactly the platform's reference rules.
    """
    if "coordination" not in document:
        return None
    declaration = document["coordination"]
    schema = json.loads(
        (ROOT / "contracts/coordination/v1/declaration.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(declaration)
    prefix = document["module_id"] + "."
    data_objects = {obj["id"] for obj in document["data_objects"]}
    graph_machines = {
        machine["id"]: machine
        for machine in document.get("module_graph", {}).get("state_machines", [])
        if machine["scope"] == "business"
    }
    machine_ids = set()
    for machine in declaration["machines"]:
        if machine["id"] in machine_ids:
            raise ValueError("coordination_machine_duplicate")
        machine_ids.add(machine["id"])
        if not machine["id"].startswith(prefix):
            raise ValueError("coordination_machine_namespace")
        if not set(machine["object_types"]).issubset(data_objects):
            raise ValueError("coordination_object_type")
        if any(not value.startswith(prefix) for value in machine["object_types"]):
            raise ValueError("coordination_object_namespace")
        if not set(machine["public_states"]).issubset(machine["states"]):
            raise ValueError("coordination_public_states")
        if document["registration_version"] == "1.1":
            actual = graph_machines.get(machine["id"])
            if actual is None or set(machine["states"]) != {
                state["id"] for state in actual["states"]
            }:
                raise ValueError("coordination_graph_machine")
    accepts = set()
    for item in declaration["accepts"]:
        if item["source_module"] == document["module_id"]:
            raise ValueError("coordination_self_subscription")
        identity = (item["source_module"], item["kind"])
        if identity in accepts:
            raise ValueError("coordination_accept_duplicate")
        accepts.add(identity)
    return declaration
