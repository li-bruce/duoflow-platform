"""A real remote workflow waits for module state and passes an authorized projection."""

import asyncio
from copy import deepcopy
import secrets
import shutil
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app.db import Invocation, Run
from app.remote_services import RemoteServiceError, RemoteServiceManager
from app.runtime import Runtime
from test_module_kit import generated, platform_root, write_json, kit, bridge_http, start_node


def test_partial_remote_flow_waits_for_submitted_and_transfers_a_fresh_object(tmp_path):
    (tmp_path / "local").mkdir()
    (tmp_path / "remote").mkdir()
    local = generated(tmp_path / "local", name="kit.target")
    remote = generated(tmp_path / "remote", "workflow-node", "kit.source")
    write_json(
        remote / "backend/projects.example.json",
        [{"project_id": "P", "organization_id": "org-a", "title": "项目"}],
    )
    exports = kit.read_json(remote, "backend/flow-exports.json")
    partial = deepcopy(next(f for f in exports["flows"] if f["entry"]["kind"] == "workflow"))
    partial.update(id="kit.source.submit-only", title="填写并提交")
    partial["entry"]["completed_states"] = ["submitted"]
    exports["flows"].append(partial)
    write_json(remote / "backend/flow-exports.json", exports)
    registration = kit.read_json(remote, "backend/registration.json")
    registration["flow_exports"] = exports
    write_json(remote / "backend/registration.json", registration)
    base = tmp_path / "host"
    engine, sessions, actors = platform_root(base, local)
    shutil.copytree(remote / "frontend", base / "flows/packs/kit-source")
    write_json(base / "flows/installed.json", {"packs": ["kit-source"]})
    manager = RemoteServiceManager(base / "data/remote")
    runtime = Runtime(
        base,
        sessions,
        SimpleNamespace(configured=False, redact=lambda value: value),
        manager,
        base / "data",
    )
    process = None
    try:
        with bridge_http(runtime, sessions) as callback:
            token = secrets.token_urlsafe(32)
            process, url = start_node(remote, tmp_path / "records", callback, token)
            manager.save_connection(
                {
                    "id": "source",
                    "name": "来源模块",
                    "base_url": url,
                    "token": token,
                    "timeout_seconds": 10,
                }
            )
            discovered = asyncio.run(manager.discover("source"))
            runtime.access.register_definition(
                kit.read_json(remote, "backend/access-definition.json"), actor_id="manager"
            )
            admitted = asyncio.run(
                manager.admit(
                    "source",
                    discovered["revision"],
                    host_check=lambda value: runtime.check_remote_registration("source", value),
                )
            )
            assert admitted["admission"]["passed"], admitted["admission"]
            runtime.set_remote_publication("source", True, discovered["revision"])
            runtime.access.grant(
                "owner",
                "kit.source",
                "kit.source.author",
                {"project_id": "P", "organization_id": "org-a"},
                actor_id="manager",
            )
            runtime.access.grant(
                "owner",
                "kit.target",
                "kit.target.user",
                {"organization_id": "org-a"},
                actor_id="manager",
            )
            owner = actors["owner"]

            def invoke(action, payload, key):
                result = asyncio.run(runtime.invoke(owner, "kit.source." + action, payload, key))
                asyncio.run(runtime.process_run(result["run_id"]))
                with sessions() as db:
                    job = db.get(Run, result["run_id"])
                    assert job.status == "succeeded", job.error
                    return job.result["data"]["projection"]

            projection = invoke(
                "create",
                {"command": {"project_id": "P", "title": "组合来源记录", "body": "内容"}},
                "create-source",
            )
            document = {
                "composition_version": "1.0",
                "id": "partial.flow",
                "version": "1.0.0",
                "title": "提交后传递",
                "description": "提交完成后，把该对象编号交给第二个模块。",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "roles": [],
                "steps": [
                    {
                        "id": "submit",
                        "title": "提交记录",
                        "module_id": "kit.source",
                        "flow_id": "kit.source.submit-only",
                        "flow_version": "1.0.0",
                        "bindings": {
                            "object_id": {
                                "source": "literal",
                                "value": projection["object_ref"]["object_id"],
                            }
                        },
                    },
                    {
                        "id": "next",
                        "title": "传递编号",
                        "module_id": "kit.target",
                        "flow_id": "kit.target.analyze.public",
                        "flow_version": "1.0.0",
                        "bindings": {
                            "text": {
                                "source": "step",
                                "step_id": "submit",
                                "path": ["projection", "object_ref", "object_id"],
                            }
                        },
                    },
                ],
            }
            # Optional object selection may be left empty; the real shared host
            # opens and lets the user create/select an object before continuing.
            empty = deepcopy(document)
            empty["id"] = "partial.empty"
            empty["input_schema"]["properties"]["record"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
            }
            empty["steps"][0]["bindings"] = {"object_id": {"source": "input", "path": ["record"]}}
            empty_saved = runtime.compositions.save(owner, empty)
            empty_id = runtime.compositions.start_run(owner, empty_saved["id"], {}, "empty-object")
            empty_run = asyncio.run(runtime.compositions.execution.advance(owner, empty_id))
            assert empty_run["status"] == "waiting", empty_run
            assert empty_run["steps"][0]["input"] == {}
            empty_host = asyncio.run(
                runtime.workflows.get(owner, empty_run["steps"][0]["view"]["data"]["instance_id"])
            )
            assert empty_host["object_ref"] is None

            saved = runtime.compositions.save(owner, document)
            identifier = runtime.compositions.start_run(owner, saved["id"], {}, "composed")
            waiting = asyncio.run(runtime.compositions.execution.advance(owner, identifier))
            assert waiting["status"] == "waiting", waiting
            assert waiting["steps"][1]["status"] == "pending"
            instance = waiting["steps"][0]["view"]["data"]["instance_id"]
            # Moving the private cursor cannot complete the public business boundary.
            item = asyncio.run(runtime.workflows.get(owner, instance))
            assert item["projection"]["state"] == "draft"
            still_waiting = asyncio.run(runtime.compositions.execution.advance(owner, identifier))
            assert still_waiting["status"] == "waiting"
            # Stop this test's own service, then restart the same endpoint and data.
            # A temporary outage preserves the run, host and original request key.
            process.terminate()
            process.wait(timeout=5)
            process = None
            for _ in range(3):
                with pytest.raises(RemoteServiceError):
                    asyncio.run(manager.health("source"))
            assert manager.monitor_status("source")["state"] == "offline"
            unavailable = asyncio.run(runtime.compositions.execution.advance(owner, identifier))
            assert unavailable["status"] == "waiting_service", unavailable
            with sessions() as db:
                assert (
                    db.query(Invocation).filter_by(capability_id="kit.target.analyze").count() == 0
                )
            process, _ = start_node(
                remote, tmp_path / "records", callback, token, port=urlsplit(url).port
            )
            asyncio.run(manager.health("source"))
            recovered = asyncio.run(runtime.compositions.execution.advance(owner, identifier))
            assert recovered["status"] == "waiting", recovered
            assert recovered["error"] is None
            assert recovered["steps"][0]["view"]["data"]["instance_id"] == instance
            invoke(
                "submit",
                {
                    "object_id": projection["object_ref"]["object_id"],
                    "expected_revision": 1,
                    "command": {},
                },
                "submit-source",
            )
            result = asyncio.run(runtime.compositions.execution.advance(owner, identifier))
            assert result["status"] == "succeeded", result
            assert result["steps"][1]["input"]["text"] == projection["object_ref"]["object_id"]
            assert result["steps"][0]["output"]["projection"]["state"] == "submitted"
            graph = asyncio.run(runtime.compositions.graph.read(owner, identifier))
            current_states = [node["id"] for node in graph["nodes"] if node.get("current_state")]
            assert current_states == ["submit::kit.source.workflow.state.submitted"]
            with sessions() as db:
                assert (
                    db.query(Invocation).filter_by(capability_id="kit.target.analyze").count() == 1
                )
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
        engine.dispose()
