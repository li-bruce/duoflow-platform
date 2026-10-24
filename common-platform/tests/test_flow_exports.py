"""Public flow boundaries must agree with real calls, states and permissions."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from app.errors import PlatformError
from app.flow_exports import resolved_flows, validate_flow_exports

ROOT = Path(__file__).resolve().parents[1]


def package(template="workflow-node"):
    base = ROOT / "templates/module-kit/blueprints" / template

    def read(path):
        return json.loads(
            (base / path)
            .read_text(encoding="utf-8")
            .replace("__MODULE_ID__", "sample.review")
            .replace("__TITLE__", "示例")
            .replace("__PACK_NAME__", "sample-review")
        )

    registration = (
        read("backend/registration.json")
        if template == "workflow-node"
        else {
            "permissions": {
                "mode": "platform_access",
                "definition": read("backend/access-definition.json"),
            },
            "data_objects": [],
        }
    )
    return (
        read("backend/flow-exports.json"),
        read("backend/manifest.json"),
        registration,
        read("backend/module-graph.json"),
        {"workflows": read("frontend/flow.json")["flows"] if template == "workflow-node" else []},
    )


@pytest.mark.parametrize("template", ["tool-python", "workflow-node"])
def test_public_entries_resolve_actual_io_without_sharing_mutable_contracts(template):
    exports, manifest, registration, graph, hosts = package(template)
    flows = resolved_flows(exports, manifest, registration, graph, **hosts)
    assert flows[0]["input_schema"] == manifest["capabilities"][0]["input_schema"]
    assert flows[0]["required_permissions"] and flows[0]["role_templates"]
    flows[0]["input_schema"].clear()
    assert manifest["capabilities"][0]["input_schema"]


def test_deliberate_partial_flow_uses_a_real_completion_boundary():
    exports, manifest, registration, graph, hosts = package()
    partial = deepcopy(exports["flows"][-1])
    partial["id"] = "sample.review.submission"
    partial["entry"]["completed_states"] = ["submitted"]
    exports["flows"].append(partial)
    flows = resolved_flows(exports, manifest, registration, graph, **hosts)
    assert flows[-1]["entry"]["completed_states"] == ["submitted"]
    assert flows[-1]["input_schema"]["properties"]["object_id"]
    # Starting a collaborative workflow does not require all later actors' rights.
    assert flows[-1]["required_permissions"] == ["sample.review.read"]


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "duplicate",
        "namespace",
        "entry",
        "entry_version",
        "node",
        "unrelated_node",
        "permission",
        "data_missing",
        "data_unknown",
        "data_operation",
        "completion",
        "projection_output",
        "code",
    ],
)
def test_invalid_or_weakened_exports_are_rejected(case):
    exports, manifest, registration, graph, hosts = package()
    first, workflow = exports["flows"][0], exports["flows"][-1]
    if case == "version":
        exports["module_version"] = "0.0.0"
    elif case == "duplicate":
        exports["flows"].append(deepcopy(first))
    elif case == "namespace":
        first["id"] = "other.module.public"
    elif case == "entry":
        first["entry"]["id"] = "sample.review.unknown"
    elif case == "entry_version":
        first["entry"]["version"] = "0.0.0"
    elif case == "node":
        first["node_ids"] = ["sample.review.missing"]
    elif case == "unrelated_node":
        first["node_ids"] = exports["flows"][1]["node_ids"]
    elif case == "permission":
        first["required_permissions"] = []
    elif case == "data_missing":
        first["data_access"] = []
    elif case == "data_unknown":
        first["data_access"][0]["object_id"] = "sample.review.unknown"
    elif case == "data_operation":
        first["data_access"][0]["operations"] = ["delete"]
    elif case == "completion":
        workflow["entry"]["completed_states"] = ["invented"]
    elif case == "projection_output":
        cap = next(c for c in manifest["capabilities"] if c["id"] == "sample.review.read")
        cap["output_schema"] = {"type": "object"}
    elif case == "code":
        first["transform_script"] = "arbitrary()"
    with pytest.raises((PlatformError, ValidationError)):
        validate_flow_exports(exports, manifest, registration, graph, **hosts)
