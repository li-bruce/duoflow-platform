"""Standalone behavior and refusal checks without a running platform."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
        ignore = (package / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('!.env.example', ignore)
        self.assertIn('.stategraph/collector.json', ignore)
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
        (package / "behavior.json").write_text(
            json.dumps({"schema_version": 3, "nodes": [{"id": "operation", "kind": "action"}]})
        )
        value = {
            "version": 1,
            "discovery": "discovery.json",
            "intent": "intent.json",
            "model": "behavior.json",
            "acceptance": "acceptance.json",
            "capabilities": {cap["id"]: ["operation"] for cap in caps},
            "states": {},
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


if __name__ == "__main__":
    unittest.main()
