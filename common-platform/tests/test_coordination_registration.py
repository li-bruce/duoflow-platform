"""Optional coordination declarations use the same portable reference rules."""

from copy import deepcopy
import importlib.util
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.coordination_contract import validate_coordination
from app.registration_contract import FEATURES, make_offer, validate_registration

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get("NODE_EXE") or shutil.which("node")
MODULE = "example.intake"


def documents():
    folder = ROOT / "templates/module-kit/blueprints/workflow-node/backend"

    def read(name):
        return json.loads((folder / name).read_text(encoding="utf-8")
                          .replace("__MODULE_ID__", MODULE).replace("__TITLE__", "合成汇交"))

    manifest, registration = read("manifest.json"), read("registration.json")
    machine = registration["module_graph"]["state_machines"][0]
    registration["coordination"] = {
        "version": "1.0",
        "machines": [{"id": machine["id"], "object_types": [MODULE + ".record"],
                      "states": [state["id"] for state in machine["states"]],
                      "public_states": ["approved"], "approval_required": True}],
        "emits": ["state.changed", "publication.requested", "resource.published", "resource.withdrawn"],
        "accepts": [{"source_module": "example.governance", "kind": "resource.published", "mode": "sync"}],
    }
    return manifest, registration


def test_optional_absent_and_empty_remain_compatible():
    manifest, registration = documents()
    registration.pop("coordination")
    assert validate_coordination(registration) is None
    assert validate_registration(registration, manifest) == registration
    registration["coordination"] = {"version": "1.0", "machines": [], "emits": [], "accepts": []}
    assert validate_registration(registration, manifest) == registration


def test_registration_checks_machine_and_features_without_enabling_subscription():
    manifest, registration = documents()
    assert validate_registration(registration, manifest) == registration
    assert "coordination-1.0" in make_offer(manifest, registration, "a" * 32)["platform_features"]
    assert set(registration["coordination"]["accepts"][0]) == {"source_module", "kind", "mode"}
    changed = deepcopy(registration)
    changed["coordination"]["machines"][0]["states"].reverse()
    assert validate_registration(changed, manifest) == changed


def mutate(document, case):
    declaration = document["coordination"]
    machine = declaration["machines"][0]
    if case == "unknown-field": declaration["callback_url"] = "https://example.test/callback"
    elif case == "machine-extra": machine["payload"] = {"text": "body"}
    elif case == "wrong-version": declaration["version"] = "2.0"
    elif case == "duplicate-machine": declaration["machines"].append(deepcopy(machine))
    elif case == "foreign-machine": machine["id"] = "other.module.lifecycle"
    elif case == "undeclared-object": machine["object_types"] = [MODULE + ".unknown"]
    elif case == "foreign-object": machine["object_types"] = ["other.module.record"]
    elif case == "missing-graph-machine": machine["id"] = MODULE + ".other-machine"
    elif case == "component-machine": document["module_graph"]["state_machines"][0]["scope"] = "component"
    elif case == "unknown-state": machine["states"].append("published")
    elif case == "missing-state": machine["states"].remove("draft")
    elif case == "duplicate-state": machine["states"].append("draft")
    elif case == "unknown-public": machine["public_states"] = ["published"]
    elif case == "nonboolean-approval": machine["approval_required"] = "yes"
    elif case == "empty-object-types": machine["object_types"] = []
    elif case == "empty-states": machine["states"] = []
    elif case == "duplicate-emits": declaration["emits"] = ["state.changed", "state.changed"]
    elif case == "unknown-event": declaration["emits"] = ["business.execute"]
    elif case == "internal-approval-subscription": declaration["accepts"][0]["kind"] = "publication.approved"
    elif case == "private-request-subscription": declaration["accepts"][0]["kind"] = "publication.requested"
    elif case == "oversized-state": machine["states"] = ["s" * 129]; machine["public_states"] = []
    elif case == "oversized-public-state": machine["public_states"] = ["s" * 129]
    elif case == "self-subscription": declaration["accepts"][0]["source_module"] = MODULE
    elif case == "wildcard": declaration["accepts"][0]["source_module"] = "*"
    elif case == "url": declaration["accepts"][0]["source_module"] = "https://example.test/"
    elif case == "extra-subscription": declaration["accepts"][0]["payload"] = {"x": True}
    elif case == "wrong-mode": declaration["accepts"][0]["mode"] = "execute"
    elif case == "duplicate-subscription": declaration["accepts"].append(deepcopy(declaration["accepts"][0]))
    elif case == "conflicting-subscription": declaration["accepts"].append({**declaration["accepts"][0], "mode": "notify"})
    elif case == "too-many-machines": declaration["machines"] *= 33
    elif case == "too-many-states": machine["states"] = ["state" + str(index) for index in range(101)]
    elif case == "too-many-subscriptions": declaration["accepts"] = [
        {"source_module": "source.module" + str(index), "kind": "resource.published", "mode": "sync"}
        for index in range(129)]
    elif case == "null": document["coordination"] = None
    else: raise AssertionError(case)


BAD_CASES = [
    "unknown-field", "machine-extra", "wrong-version", "duplicate-machine", "foreign-machine",
    "undeclared-object", "foreign-object", "missing-graph-machine", "component-machine",
    "unknown-state", "missing-state", "duplicate-state", "unknown-public", "nonboolean-approval",
    "empty-object-types", "empty-states", "duplicate-emits", "unknown-event",
    "internal-approval-subscription", "self-subscription", "wildcard", "url",
    "private-request-subscription", "oversized-state", "oversized-public-state",
    "extra-subscription", "wrong-mode", "duplicate-subscription", "conflicting-subscription",
    "too-many-machines", "too-many-states", "too-many-subscriptions", "null",
]


@pytest.mark.parametrize("case", BAD_CASES)
def test_platform_and_offline_reference_validator_reject_inconsistent_declaration(case):
    manifest, document = documents()
    mutate(document, case)
    with pytest.raises((ValueError, ValidationError)):
        validate_coordination(document)
    with pytest.raises((ValueError, ValidationError)):
        validate_registration(document, manifest)


def test_version_10_can_describe_own_machine_without_module_graph():
    _, document = documents()
    document["registration_version"] = "1.0"
    document.pop("module_graph")
    assert validate_coordination(document) == document["coordination"]


def test_state_identifier_boundary_is_128_while_machine_identifier_can_be_160():
    sdk = importlib.import_module("templates.module-kit.sdk.python.registration")
    _, document = documents()
    document["registration_version"] = "1.0"
    document.pop("module_graph")
    machine = document["coordination"]["machines"][0]
    machine["id"] = MODULE + "." + "m" * (159 - len(MODULE))
    machine["states"] = ["s" * 128]
    machine["public_states"] = ["s" * 128]
    assert validate_coordination(document) == document["coordination"]
    sdk.Registration._validate_coordination(document)
    machine["states"] = ["s" * 129]
    machine["public_states"] = ["s" * 129]
    with pytest.raises(ValidationError):
        validate_coordination(document)
    with pytest.raises(sdk.RegistrationError):
        sdk.Registration._validate_coordination(document)


def test_canonical_schema_matches_both_registration_versions():
    canonical = json.loads((ROOT / "contracts/coordination/v1/declaration.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(canonical)
    canonical.pop("$schema")
    for folder in ("v1", "v1.1"):
        schema = json.loads((ROOT / f"contracts/registration/{folder}/definition.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["properties"]["coordination"] == canonical
        assert "coordination" not in schema["required"]


def test_pure_validator_extracts_without_platform_dependencies(tmp_path):
    package = tmp_path / "portable"
    target = package / "app/coordination_contract.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "app/coordination_contract.py", target)
    contracts = package / "contracts/coordination/v1"
    contracts.mkdir(parents=True)
    shutil.copyfile(ROOT / "contracts/coordination/v1/declaration.schema.json", contracts / "declaration.schema.json")
    spec = importlib.util.spec_from_file_location("standalone_coordination_contract", target)
    portable = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(portable)
    _, document = documents()
    assert portable.validate_coordination(document) == document["coordination"]
    mutate(document, "undeclared-object")
    with pytest.raises(ValueError, match="coordination_object_type"):
        portable.validate_coordination(document)


@pytest.mark.parametrize("case", BAD_CASES)
def test_python_sdk_checks_same_reference_rules(case):
    sdk = importlib.import_module("templates.module-kit.sdk.python.registration")
    _, document = documents()
    mutate(document, case)
    with pytest.raises(sdk.RegistrationError):
        sdk.Registration._validate_coordination(document)


def test_python_sdk_requires_platform_coordination_feature_and_preserves_optional_compatibility():
    sdk = importlib.import_module("templates.module-kit.sdk.python.registration")
    manifest, document = documents()

    def create(registration):
        return sdk.Registration(manifest, registration,
                                {cap["id"]: lambda: None for cap in manifest["capabilities"]},
                                {operation: lambda: None for operation in registration["workflow_operations"]},
                                graph=registration["module_graph"], runtime_probe=lambda _: True)

    for declaration in (document["coordination"], {"version": "1.0", "machines": [], "emits": [], "accepts": []}):
        document["coordination"] = declaration
        registrar = create(document)
        offer = {**make_offer(manifest, document, "a" * 32), "runtime_probe": {}}
        assert registrar.confirm(offer)["accepted"] is True
        offer["platform_features"] = [feature for feature in FEATURES if feature != "coordination-1.0"]
        with pytest.raises(sdk.RegistrationError):
            registrar.confirm(offer)
    document.pop("coordination")
    registrar = create(document)
    offer = {**make_offer(manifest, document, "a" * 32), "runtime_probe": {},
             "platform_features": [feature for feature in FEATURES if feature != "coordination-1.0"]}
    assert registrar.confirm(offer)["accepted"] is True


@pytest.mark.skipif(not NODE, reason="Node runtime unavailable")
def test_node_registration_checks_same_references_and_platform_feature(tmp_path):
    manifest, registration = documents()
    fixtures = [{"case": "valid", "manifest": manifest, "registration": registration}]
    for case in BAD_CASES:
        document = deepcopy(registration)
        mutate(document, case)
        fixtures.append({"case": case, "manifest": manifest, "registration": document})
    fixture_path = tmp_path / "fixtures.json"
    fixture_path.write_text(json.dumps(fixtures), encoding="utf-8")
    script = tmp_path / "node-registration.mjs"
    script.write_text("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createRegistration } from %s;
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const features = %s;
function create({manifest, registration}) {
  return createRegistration({manifest, registration, graph: registration.module_graph,
    handlers: Object.fromEntries(manifest.capabilities.map(cap => [cap.id, () => {}])),
    workflowHandlers: Object.fromEntries(registration.workflow_operations.map(id => [id, () => {}])),
    compile: () => {}, runtimeProbe: () => true});
}
for (const item of fixtures) {
  if (item.case === 'valid') create(item);
  else assert.throws(() => create(item), item.case);
}
const item = fixtures[0], registrar = create(item);
const offer = {registration_version: '1.1', nonce: 'a'.repeat(32), contract_digest: 'b'.repeat(64),
  manifest: item.manifest, registration: item.registration, runtime_probe: {}, platform_features: features};
assert.equal((await registrar.confirm(offer)).accepted, true);
assert.throws(() => registrar.confirm({...offer, platform_features: features.filter(f => f !== 'coordination-1.0')}));
const empty = structuredClone(item);
empty.registration.coordination = {version: '1.0', machines: [], emits: [], accepts: []};
const emptyRegistrar = create(empty);
assert.equal((await emptyRegistrar.confirm({...offer, registration: empty.registration})).accepted, true);
assert.throws(() => emptyRegistrar.confirm({...offer, registration: empty.registration,
  platform_features: features.filter(f => f !== 'coordination-1.0')}));
const absent = structuredClone(item);
delete absent.registration.coordination;
assert.equal((await create(absent).confirm({...offer, registration: absent.registration,
  platform_features: features.filter(f => f !== 'coordination-1.0')})).accepted, true);
const boundary = structuredClone(item);
boundary.registration.registration_version = '1.0';
delete boundary.registration.module_graph;
const machine = boundary.registration.coordination.machines[0];
machine.id = boundary.registration.module_id + '.' + 'm'.repeat(159 - boundary.registration.module_id.length);
machine.states = ['s'.repeat(128)];
machine.public_states = ['s'.repeat(128)];
create(boundary);
machine.states = ['s'.repeat(129)];
machine.public_states = ['s'.repeat(129)];
assert.throws(() => create(boundary));
console.log(JSON.stringify({negative_cases: fixtures.length - 1, feature: 'coordination-1.0'}));
""" % (json.dumps((ROOT / "templates/module-kit/sdk/node/registration.mjs").as_uri()), json.dumps(FEATURES)), encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(fixture_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["negative_cases"] == len(BAD_CASES)
