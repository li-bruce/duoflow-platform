"""Atomic declarations must agree with real interfaces and independent bindings."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from app.errors import PlatformError
from app.module_graph import resolved_atoms, validate_graph_hosts, validate_module_graph

ROOT = Path(__file__).resolve().parents[1]


def package(template="workflow-node"):
    root = ROOT / "templates/module-kit/blueprints" / template

    def read(name):
        text = (
            (root / name)
            .read_text(encoding="utf-8")
            .replace("__MODULE_ID__", "sample.review")
            .replace("__TITLE__", "合成登记")
            .replace("__PACK_NAME__", "sample-review")
        )
        return json.loads(text)

    graph, manifest = read("backend/module-graph.json"), read("backend/manifest.json")
    hosts = (
        {"workflows": read("frontend/flow.json")["flows"]}
        if template == "workflow-node"
        else {"interactions": [read("frontend/interaction.json")]}
    )
    return graph, manifest, hosts


@pytest.mark.parametrize("template", ["tool-python", "workflow-node"])
def test_real_template_contracts_and_resolved_backend_schemas(template):
    graph, manifest, hosts = package(template)
    actual = validate_graph_hosts(graph, manifest, **hosts)
    assert actual == graph and actual is not graph
    cap = manifest["capabilities"][0]
    atom = next(
        a
        for a in resolved_atoms(graph, manifest)
        if a["kind"] == "backend" and a["capability"]["id"] == cap["id"]
    )
    assert atom["input_schema"] == cap["input_schema"]
    assert atom["output_schema"] == cap["output_schema"]
    assert atom["mappings"]
    atom["input_schema"].clear()
    assert manifest["capabilities"][0]["input_schema"]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_atom",
        "missing_atom",
        "unmapped_atom",
        "missing_node",
        "unknown_event",
        "bad_initial",
        "bad_schema_reference",
        "wrong_capability_version",
        "unknown_capability",
        "duplicate_host",
        "invalid_transition",
        "bad_state_node",
        "unknown_field",
        "source_escape",
    ],
)
def test_invalid_atomic_contracts_fail_before_execution(case):
    graph, manifest, _ = package("tool-python")
    frontend = next(a for a in graph["atoms"] if a["kind"] == "frontend")
    if case == "duplicate_atom":
        graph["atoms"].append(deepcopy(graph["atoms"][0]))
    elif case == "missing_atom":
        graph["atoms"].pop(0)
    elif case == "unmapped_atom":
        graph["mappings"] = [
            m for m in graph["mappings"] if m["atom_id"] != graph["atoms"][0]["id"]
        ]
    elif case == "missing_node":
        graph["mappings"][0]["node_id"] = "sample.review.unknown"
    elif case == "unknown_event":
        graph["mappings"][-1]["event_id"] = "not-registered"
    elif case == "bad_initial":
        frontend["initial_state"] = {"text": False}
    elif case == "bad_schema_reference":
        frontend["input_schema"] = {"type": "object", "$ref": "https://invalid.example/schema"}
    elif case == "wrong_capability_version":
        graph["atoms"][0]["capability"]["version"] = "2.0.0"
    elif case == "unknown_capability":
        graph["atoms"][0]["capability"]["id"] = "sample.review.missing"
    elif case == "duplicate_host":
        copy = deepcopy(frontend)
        copy["id"] += ".another"
        graph["atoms"].append(copy)
    elif case == "invalid_transition":
        graph["state_machines"][0]["transitions"][0]["to"] = "absent"
    elif case == "bad_state_node":
        graph["state_machines"][0]["states"][0]["node_id"] = graph["nodes"][0]["id"]
    elif case == "unknown_field":
        graph["execute_script"] = "untrusted()"
    elif case == "source_escape":
        graph["atoms"][0]["source"]["path"] = "../../secret.py"
    with pytest.raises((ValueError, PlatformError, ValidationError)):
        validate_module_graph(graph, manifest)


@pytest.mark.parametrize(
    "case",
    [
        "missing_step",
        "missing_event",
        "wrong_event_input",
        "wrong_binding",
        "wrong_business_state",
        "wrong_transition",
        "missing_business_machine",
    ],
)
def test_graph_cannot_claim_a_host_contract_different_from_implementation(case):
    graph, manifest, hosts = package()
    frontend = next(a for a in graph["atoms"] if a["kind"] == "frontend")
    if case == "missing_step":
        graph["atoms"].remove(frontend)
        graph["mappings"] = [m for m in graph["mappings"] if m["atom_id"] != frontend["id"]]
    elif case == "missing_event":
        frontend["events"] = [e for e in frontend["events"] if "capability" not in e]
    elif case == "wrong_event_input":
        frontend["events"][0]["input_schema"] = {"type": "object"}
    elif case == "wrong_binding":
        frontend["host"]["step_id"] = "absent"
    elif case == "wrong_business_state":
        graph["state_machines"][0]["initial"] = "approved"
    elif case == "wrong_transition":
        graph["state_machines"][0]["transitions"][0]["to"] = "approved"
    elif case == "missing_business_machine":
        graph["state_machines"][0]["scope"] = "execution"
        graph["state_machines"][0]["owner_id"] = frontend["id"]
    with pytest.raises((ValueError, PlatformError, ValidationError)):
        validate_graph_hosts(graph, manifest, **hosts)


def test_python_startup_binds_actual_functions_without_calling_them():
    spec = importlib.util.spec_from_file_location(
        "atomic_sdk", ROOT / "templates/module-kit/sdk/python/atoms.py"
    )
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)
    graph, manifest, _ = package("tool-python")
    calls = []

    def handler(*args):
        calls.append(args)

    cap = manifest["capabilities"][0]["id"]
    bindings = sdk.AtomBindings(graph, manifest, {cap: handler})
    assert bindings.for_capability(cap)["capability"]["id"] == cap
    assert calls == []
    graph["atoms"].clear()
    assert bindings.document["atoms"]
    with pytest.raises(ValueError):
        sdk.AtomBindings(bindings.document, manifest, {cap: None})
    with pytest.raises(ValueError):
        sdk.AtomBindings(bindings.document, manifest, {})


def test_one_atom_can_map_to_multiple_business_nodes_without_duplicate_definition():
    graph, manifest, _ = package("tool-python")
    graph["nodes"].append(
        {
            "id": "sample.review.other",
            "kind": "business",
            "title": "复用",
            "description": "同一功能用于另一业务节点。",
        }
    )
    graph["mappings"].append(
        {"atom_id": graph["atoms"][0]["id"], "node_id": "sample.review.other", "role": "handles"}
    )
    assert (
        validate_module_graph(graph, manifest)["mappings"][-1]["node_id"] == "sample.review.other"
    )
