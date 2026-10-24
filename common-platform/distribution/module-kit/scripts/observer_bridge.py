"""Verify Observer provenance and link a module to its real business model.

This development tool never changes business state. Installation uses the pinned
upstream upgrader. Binding is structural; gate additionally requires upstream
change_sync evidence, so a saved map cannot masquerade as tested business logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    """Use normalized UTF-8 identity across Windows and POSIX checkouts."""
    return hashlib.sha256(
        path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode()
    ).hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Required project file is missing or outside the module")
    return path


def source_check(source):
    """Verify the exact reviewed commit and all locked Skill files before use."""
    source = source.resolve()
    lock = read(ROOT / "business-observer.lock.json")
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != lock["commit"]:
        raise ValueError("Observer version differs from business-observer.lock.json")
    for relative, expected in lock["files"].items():
        if digest(inside(source, relative)) != expected:
            raise ValueError("Observer source differs from the reviewed version")
    return {
        "status": "verified",
        "repository": lock["repository"],
        "commit": revision,
        "files": len(lock["files"]),
    }


def init(module, source):
    """Install upstream query, validation and viewer support without making a model."""
    source_check(source)
    script = source / "skills/business-observer/scripts/bo_upgrade.py"
    command = [
        sys.executable,
        str(script),
        "apply",
        "--root",
        str(module),
        "--runtime",
        "observer_runtime",
        "--viewer",
        "static/graph",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    return {
        "status": "initialized",
        "business_model": "required",
        "runtime_evidence": "not_checked",
    }


def check_mapping(module, mapping):
    """Require coverage of public capabilities and declared business states."""
    definition = read(inside(module, mapping))
    required = {
        "version",
        "discovery",
        "intent",
        "model",
        "acceptance",
        "capabilities",
        "states",
    }
    if definition.get("version") == 2:
        required |= {"module_graph", "flow_exports", "atoms", "nodes", "flows"}
    if (
        not required <= set(definition)
        or set(definition) - required - {"project"}
        or definition["version"] not in (1, 2)
    ):
        raise ValueError("Unsupported Observer mapping contract")
    for key in ("discovery", "intent", "model", "acceptance"):
        inside(module, definition[key])
    model = read(inside(module, definition["model"]))
    if definition.get("project"):
        project = read(inside(module, definition["project"]))
        if project.get("protocol") != "bo-project/v1" or project.get("model") != model:
            raise ValueError("The generated model differs from its canonical Observer project")
    if model.get("schema_version") != 3:
        raise ValueError("The active business model must use Observer model v3")
    nodes = {node["id"]: node for node in model["nodes"]}
    metadata = read(module / "module-kit.json")
    if metadata.get("contracts", {}).get("module_graph") and definition["version"] != 2:
        raise ValueError("Graph-aware modules require Observer mapping version 2")
    manifest = read(module / "backend/manifest.json")
    capabilities = {cap["id"] for cap in manifest["capabilities"]}
    if set(definition["capabilities"]) != capabilities:
        raise ValueError("Every public capability needs an explicit business-model mapping")
    for references in definition["capabilities"].values():
        if (
            not references
            or len(references) != len(set(references))
            or any(node not in nodes for node in references)
        ):
            raise ValueError("Capability mapping contains missing or duplicate nodes")
    flows = (
        read(module / "frontend/flow.json")["flows"]
        if (module / "frontend/flow.json").exists()
        else []
    )
    if set(definition["states"]) != {flow["id"] for flow in flows}:
        raise ValueError("Every workflow needs an explicit state mapping")
    for flow in flows:
        machine = flow.get("state_machine")
        if not machine:
            raise ValueError("A newly modeled workflow must declare its business states")
        states = definition["states"][flow["id"]]
        if set(states) != {state["id"] for state in machine["states"]}:
            raise ValueError("State mapping must cover every public business state")
        if any(nodes.get(node, {}).get("kind") != "state" for node in states.values()):
            raise ValueError("State mappings must reference actual state nodes")
    if definition["version"] == 2:
        graph = read(inside(module, definition["module_graph"]))
        graph_nodes = {node["id"]: node for node in graph["nodes"]}
        atoms = {atom["id"]: atom for atom in graph["atoms"]}
        if set(definition["nodes"]) != set(graph_nodes) or set(definition["atoms"]) != set(atoms):
            raise ValueError("Every public node and atom needs an Observer mapping")
        for public_id, model_id in definition["nodes"].items():
            if model_id not in nodes or (
                graph_nodes[public_id]["kind"] == "state" and nodes[model_id]["kind"] != "state"
            ):
                raise ValueError(
                    "Public node mapping references a missing or incompatible model node"
                )
        for atom_id, references in definition["atoms"].items():
            expected = {
                definition["nodes"][entry["node_id"]]
                for entry in graph["mappings"]
                if entry["atom_id"] == atom_id
            }
            if (
                not references
                or len(set(references)) != len(references)
                or set(references) != expected
            ):
                raise ValueError("Atomic mapping differs from the public graph mappings")
            atom = atoms[atom_id]
            if (
                atom["kind"] == "backend"
                and set(definition["capabilities"][atom["capability"]["id"]]) != expected
            ):
                raise ValueError("Capability and backend atom map to different business nodes")
        exports = read(inside(module, definition["flow_exports"]))
        if set(definition["flows"]) != {flow["id"] for flow in exports["flows"]}:
            raise ValueError("Every public business flow needs an Observer mapping")
        for flow in exports["flows"]:
            references = definition["flows"][flow["id"]]
            if len(references) != len(set(references)) or set(references) != {
                definition["nodes"][node] for node in flow["node_ids"]
            }:
                raise ValueError("Public flow mapping differs from its declared business boundary")
    return definition


def check_sync_inputs(module, definition):
    """Bind public mappings to the exact files used by change_sync's receipt.

    Upstream reads the model and acceptance from sync.json, the intent from that
    model, and the optional discovery from sync.json. A project envelope and its
    explicitly mapped generated model are both supported; an unrelated copy is
    not interchangeable with the file covered by the accepted candidate.
    """
    config = read(inside(module, "sync.json"))
    if not isinstance(config, dict):
        raise ValueError("Observer synchronization configuration must be an object")

    def referenced_file(relative):
        if not isinstance(relative, str) or not relative:
            raise ValueError("Observer synchronization requires linked project files")
        return inside(module, relative)

    model_path = referenced_file(definition["model"])
    allowed_models = {model_path}
    if definition.get("project"):
        allowed_models.add(referenced_file(definition["project"]))
    sync_model_path = referenced_file(config.get("model"))
    if sync_model_path not in allowed_models:
        raise ValueError("Observer synchronization uses a different business model")
    sync_model = read(sync_model_path)
    if isinstance(sync_model, dict) and sync_model.get("protocol") == "bo-project/v1":
        sync_model = sync_model.get("model")
    if not isinstance(sync_model, dict) or sync_model != read(model_path):
        raise ValueError("Observer synchronization model differs from the public mapping")

    actual_inputs = {
        "intent": sync_model.get("intent"),
        "acceptance": config.get("acceptance"),
        "discovery": config.get("discovery"),
    }
    for key, relative in actual_inputs.items():
        if referenced_file(relative) != referenced_file(definition[key]):
            raise ValueError("Observer synchronization uses a different " + key + " file")
    if definition.get("version") == 2:
        graph_paths = {referenced_file(definition[key]) for key in ("module_graph", "flow_exports")}
        tracked = [
            *config.get("code_files", []),
            *config.get("watch_files", []),
            *sync_model.get("source_files", []),
        ]
        if not graph_paths <= {referenced_file(path) for path in tracked}:
            raise ValueError(
                "The atomic graph and public flows must be tracked by Observer synchronization"
            )


def gate(module, source, mapping):
    """Run module checks, source checks and the upstream evidence gate together."""
    source_check(source)
    definition = check_mapping(module, mapping)
    check_sync_inputs(module, definition)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/module_kit.py"), "check", str(module)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    scripts = source / "skills/business-observer/scripts"
    commands = [
        [
            str(scripts / "requirements_dialogue.py"),
            "check",
            "--file",
            definition["discovery"],
            "--intent",
            definition["intent"],
            "--model",
            definition["model"],
            "--acceptance",
            definition["acceptance"],
        ],
        [str(scripts / "change_sync.py"), "check", "--root", str(module)],
        [str(scripts / "architecture_guard.py"), "check", "--root", str(module)],
    ]
    if definition.get("project"):
        commands.insert(
            0,
            [
                str(scripts / "bo.py"),
                "validate",
                "--root",
                str(module),
                "--project",
                definition["project"],
            ],
        )
    for command in commands:
        subprocess.run(
            [sys.executable, *command],
            cwd=module,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    return {
        "status": "passed",
        "checks": [
            "module_contract",
            "observer_source",
            "business_mapping",
            "sync_inputs",
            "requirements",
            "change_sync",
            "architecture",
        ],
        "platform_admission": "required_separately",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("source", "init", "mapping", "gate"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--module", type=Path)
    parser.add_argument("--mapping", default="duoflow-observer.json")
    args = parser.parse_args()
    try:
        if args.command != "mapping" and not args.source:
            raise ValueError("--source is required")
        if args.command != "source" and (not args.module or not args.module.is_dir()):
            raise ValueError("--module must identify an existing module")
        module = args.module.resolve() if args.module else None
        if args.command == "source":
            result = source_check(args.source)
        elif args.command == "init":
            result = init(module, args.source.resolve())
        elif args.command == "mapping":
            check_mapping(module, args.mapping)
            result = {"status": "mapped", "runtime_evidence": "not_checked"}
        else:
            result = gate(module, args.source.resolve(), args.mapping)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # Raw subprocess output may contain local paths or application inputs.
        print(
            json.dumps(
                {"status": "failed", "error_type": type(error).__name__},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
