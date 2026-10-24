"""Standalone behavior and refusal checks without a running platform."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kit = load("standalone_kit", ROOT / "scripts/module_kit.py")
observer = load("observer_bridge", ROOT / "scripts/observer_bridge.py")


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def package(self, template="tool-python"):
        target = self.root / template
        kit.scaffold(template, "example.test", "合成模块", target)
        return target

    def test_python_package_and_real_business_test(self):
        package = self.package()
        ignore = (package / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!.env.example", ignore)
        self.assertIn(".stategraph/collector.json", ignore)
        self.assertEqual(kit.check(package)["status"], "valid")
        result = subprocess.run(
            [sys.executable, str(package / "tests/self_test.py")], capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_node_registration_rejects_unknown_capability(self):
        package = self.package("workflow-node")
        path = package / "backend/registration.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["business_chains"][0]["steps"].append("unknown.other")
        path.write_text(json.dumps(value))
        with self.assertRaises(Exception):
            kit.check(package)

    def test_existing_output_is_preserved(self):
        package = self.package()
        (package / "keep.txt").write_text("keep")
        with self.assertRaises(kit.KitError):
            kit.scaffold("tool-python", "example.test", "合成模块", package)
        self.assertEqual((package / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_supplier_code_is_not_imported(self):
        package = self.package()
        path = package / "backend/handlers.py"
        path.write_text(
            'raise RuntimeError("must not execute")\n' + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.assertFalse(kit.check(package)["executed_supplier_code"])

    def test_unknown_state_is_rejected(self):
        package = self.package("workflow-node")
        path = package / "frontend/flow.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["flows"][0]["state_machine"]["transitions"][0]["to"] = "missing"
        path.write_text(json.dumps(value))
        with self.assertRaises(Exception):
            kit.check(package)

    def test_malformed_frontend_is_rejected(self):
        package = self.package()
        (package / "frontend/Component.tsx").write_text("export default function (")
        with self.assertRaises(Exception):
            kit.check(package)

    def mapping(self, package):
        caps = json.loads((package / "backend/manifest.json").read_text(encoding="utf-8"))[
            "capabilities"
        ]
        for name in ("discovery.json", "intent.json", "acceptance.json"):
            (package / name).write_text("{}")
        graph = json.loads((package / "backend/module-graph.json").read_text(encoding="utf-8"))
        atomic_nodes = {
            atom["id"]: sorted(
                {m["node_id"] for m in graph["mappings"] if m["atom_id"] == atom["id"]}
            )
            for atom in graph["atoms"]
        }
        (package / "behavior.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "nodes": [
                        {"id": n["id"], "kind": "state" if n["kind"] == "state" else "action"}
                        for n in graph["nodes"]
                    ],
                }
            )
        )
        value = {
            "version": 2,
            "discovery": "discovery.json",
            "intent": "intent.json",
            "model": "behavior.json",
            "acceptance": "acceptance.json",
            "capabilities": {cap["id"]: atomic_nodes[cap["id"] + ".backend"] for cap in caps},
            "states": {},
            "module_graph": "backend/module-graph.json",
            "flow_exports": "backend/flow-exports.json",
            "flows": {
                flow["id"]: flow["node_ids"]
                for flow in json.loads(
                    (package / "backend/flow-exports.json").read_text(encoding="utf-8")
                )["flows"]
            },
            "nodes": {node["id"]: node["id"] for node in graph["nodes"]},
            "atoms": atomic_nodes,
        }
        (package / "duoflow-observer.json").write_text(json.dumps(value))
        return value

    def test_mapping_preserves_unverified_runtime(self):
        package = self.package()
        value = self.mapping(package)
        self.assertEqual(observer.check_mapping(package, "duoflow-observer.json"), value)
        self.assertFalse((package / "sync.json").exists())

    def test_missing_mapping_is_rejected(self):
        package = self.package()
        with self.assertRaises(Exception):
            observer.check_mapping(package, "duoflow-observer.json")

    def test_atomic_map_must_match_public_nodes(self):
        package = self.package()
        value = self.mapping(package)
        first = next(iter(value["atoms"]))
        value["atoms"][first] = []
        (package / "duoflow-observer.json").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Atomic mapping"):
            observer.check_mapping(package, "duoflow-observer.json")

    def test_graph_module_cannot_claim_legacy_mapping_coverage(self):
        package = self.package()
        value = self.mapping(package)
        value["version"] = 1
        for key in ("module_graph", "flow_exports", "atoms", "nodes", "flows"):
            value.pop(key)
        (package / "duoflow-observer.json").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "version 2"):
            observer.check_mapping(package, "duoflow-observer.json")

    def test_unmapped_capability_is_rejected(self):
        package = self.package()
        value = self.mapping(package)
        value["capabilities"] = {}
        (package / "duoflow-observer.json").write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            observer.check_mapping(package, "duoflow-observer.json")

    def test_outside_mapping_is_rejected(self):
        package = self.package()
        value = self.mapping(package)
        value["intent"] = "../intent.json"
        (self.root / "intent.json").write_text("{}")
        (package / "duoflow-observer.json").write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            observer.check_mapping(package, "duoflow-observer.json")

    def test_gate_rejects_missing_observer(self):
        package = self.package()
        self.mapping(package)
        with self.assertRaises(Exception):
            observer.gate(package, self.root, "duoflow-observer.json")

    def test_validation_imports_no_server_or_database(self):
        code = "import sys; sys.path.insert(0,'.'); from duoflow_validation import registry,access_control,remote_services; assert not {'fastapi','sqlalchemy','uvicorn'} & set(sys.modules)"
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


class ObserverInputTests(unittest.TestCase):
    """Public mappings must share the exact inputs accepted by change_sync."""

    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model = {"schema_version": 3, "intent": "intent.json", "nodes": []}
        self.definition = {
            "model": "behavior.json",
            "intent": "intent.json",
            "acceptance": "acceptance.json",
            "discovery": "discovery.json",
        }
        self.config = {
            "model": "behavior.json",
            "acceptance": "acceptance.json",
            "discovery": "discovery.json",
        }
        self.write("behavior.json", self.model)
        for name in ("intent.json", "acceptance.json", "discovery.json"):
            self.write(name, {})
        self.write("sync.json", self.config)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def check(self):
        observer.check_sync_inputs(self.root, self.definition)

    def test_existing_v3_layout_accepts_same_materials(self):
        self.check()

    def test_project_envelope_and_its_generated_model_are_supported(self):
        self.definition["project"] = "bo_stategraph/project.json"
        self.write(
            self.definition["project"],
            {"protocol": "bo-project/v1", "model": self.model},
        )
        for model_path in (self.definition["project"], self.definition["model"]):
            with self.subTest(model=model_path):
                self.config["model"] = model_path
                self.write("sync.json", self.config)
                self.check()

    def test_relative_aliases_of_the_same_files_are_supported(self):
        self.config = {key: "./" + value for key, value in self.config.items()}
        self.write("sync.json", self.config)
        self.check()

    def test_missing_sync_configuration_is_rejected(self):
        (self.root / "sync.json").unlink()
        with self.assertRaises(ValueError):
            self.check()

    def test_missing_linked_inputs_are_rejected(self):
        for key in ("model", "acceptance", "discovery"):
            with self.subTest(field=key):
                self.write(
                    "sync.json",
                    {name: value for name, value in self.config.items() if name != key},
                )
                with self.assertRaises(ValueError):
                    self.check()

    def test_identical_model_copy_is_not_accepted_as_the_mapped_source(self):
        self.write("other-model.json", self.model)
        self.config["model"] = "other-model.json"
        self.write("sync.json", self.config)
        with self.assertRaisesRegex(ValueError, "different business model"):
            self.check()

    def test_different_acceptance_or_discovery_files_are_rejected(self):
        self.write("other.json", {})
        for key in ("acceptance", "discovery"):
            with self.subTest(field=key):
                self.write("sync.json", {**self.config, key: "other.json"})
                with self.assertRaisesRegex(ValueError, "different " + key):
                    self.check()

    def test_model_intent_must_match_the_mapped_intent(self):
        self.write("other-intent.json", {})
        self.write("behavior.json", {**self.model, "intent": "other-intent.json"})
        with self.assertRaisesRegex(ValueError, "different intent"):
            self.check()

    def test_missing_model_intent_is_rejected(self):
        self.write(
            "behavior.json",
            {key: value for key, value in self.model.items() if key != "intent"},
        )
        with self.assertRaises(ValueError):
            self.check()

    def test_changed_project_model_is_rejected(self):
        self.definition["project"] = "bo_stategraph/project.json"
        self.write(
            self.definition["project"],
            {
                "protocol": "bo-project/v1",
                "model": {**self.model, "nodes": [{"id": "changed"}]},
            },
        )
        self.config["model"] = self.definition["project"]
        self.write("sync.json", self.config)
        with self.assertRaisesRegex(ValueError, "differs from the public mapping"):
            self.check()

    def test_gate_rejects_mismatched_inputs_before_running_commands(self):
        self.write("other-acceptance.json", {})
        self.config["acceptance"] = "other-acceptance.json"
        self.write("sync.json", self.config)
        # This checks orchestration only. It does not fabricate upstream evidence.
        with patch.object(observer, "source_check"), patch.object(
            observer, "check_mapping", return_value=self.definition
        ), patch.object(observer.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "different acceptance"):
                observer.gate(self.root, self.root, "duoflow-observer.json")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
