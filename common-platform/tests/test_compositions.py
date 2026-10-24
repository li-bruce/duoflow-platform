"""Two installed modules exercise real execution, persistence and permission boundaries."""

import asyncio
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace

import pytest

from app.composition_contract import compatible, validate_composition
from app.composition_models import CompositionRun, CompositionStep
from app.db import Invocation
from app.errors import PlatformError
from app.runtime import Runtime
from test_module_kit import generated, platform_root, write_json


@pytest.fixture
def composition_case(tmp_path):
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = generated(tmp_path / "first", name="kit.first")
    second = generated(tmp_path / "second", name="kit.second")
    base = tmp_path / "platform"
    engine, sessions, actors = platform_root(base, first)
    shutil.copytree(second / "backend", base / "extensions/kit-second")
    shutil.copytree(second / "frontend", base / "components/packs/kit-second")
    write_json(base / "extensions/installed.json", {"modules": ["kit-first", "kit-second"]})
    write_json(base / "components/installed.json", {"packs": ["kit-first", "kit-second"]})
    runtime = Runtime(
        base,
        sessions,
        SimpleNamespace(configured=False, redact=lambda value: value),
        data_dir=base / "data",
    )
    grants = [
        runtime.access.grant(
            "owner", module, module + ".user", {"organization_id": "org-a"}, actor_id="manager"
        )
        for module in ("kit.first", "kit.second")
    ]
    string = {"type": "string", "minLength": 1, "maxLength": 20000}
    document = {
        "composition_version": "1.0",
        "id": "text.chain",
        "version": "1.0.0",
        "title": "文本整理流程",
        "description": "两个独立模块顺序处理同一段文本。",
        "input_schema": {
            "type": "object",
            "properties": {"text": string},
            "required": ["text"],
            "additionalProperties": False,
        },
        "steps": [
            {
                "id": "first",
                "title": "第一步",
                "module_id": "kit.first",
                "flow_id": "kit.first.analyze.public",
                "flow_version": "1.0.0",
                "bindings": {"text": {"source": "input", "path": ["text"]}},
            },
            {
                "id": "second",
                "title": "第二步",
                "module_id": "kit.second",
                "flow_id": "kit.second.analyze.public",
                "flow_version": "1.0.0",
                "bindings": {
                    "text": {"source": "step", "step_id": "first", "path": ["normalized_text"]}
                },
            },
        ],
        "roles": [],
    }
    yield runtime, actors, document, grants
    engine.dispose()


def begin(case):
    runtime, actors, document, _ = case
    definition = runtime.compositions.save(actors["owner"], document)
    run_id = runtime.compositions.start_run(
        actors["owner"], definition["id"], {"text": " one  two \n three "}, "new-run"
    )
    return run_id


def test_two_modules_transfer_output_and_restore_without_reexecution(composition_case):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    result = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert result["status"] == "succeeded", result
    assert result["steps"][1]["input"]["text"] == result["steps"][0]["output"]["normalized_text"]
    restarted = Runtime(runtime.root, runtime.sessions, runtime.gateway, data_dir=runtime.data_dir)
    restored = asyncio.run(restarted.compositions.execution.advance(actors["owner"], run_id))
    assert restored == result
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 2


def test_definition_versions_are_immutable_and_runs_are_private(composition_case):
    runtime, actors, document, _ = composition_case
    run_id = begin(composition_case)
    changed = deepcopy(document)
    changed["title"] = "修改后的标题"
    with pytest.raises(PlatformError, match="版本"):
        runtime.compositions.save(actors["owner"], changed)
    with pytest.raises(PlatformError):
        asyncio.run(runtime.compositions.read(actors["outside"], run_id))
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0


@pytest.mark.parametrize("change", ["forward", "type", "missing", "same-name-role"])
def test_bad_connections_or_inferred_roles_cannot_be_saved(composition_case, change):
    runtime, actors, document, _ = composition_case
    if change == "forward":
        document["steps"][0]["bindings"]["text"] = {
            "source": "step",
            "step_id": "second",
            "path": ["normalized_text"],
        }
    elif change == "type":
        document["steps"][1]["bindings"]["text"]["path"] = ["character_count"]
    elif change == "missing":
        document["steps"][1]["bindings"] = {}
    else:
        document["roles"] = [
            {
                "id": "operator",
                "title": "办理人",
                "bindings": [
                    {
                        "module_id": "kit.first",
                        "role_id": "kit.second.user",
                        "scope": {"organization_id": "org-a"},
                    }
                ],
            }
        ]
    with pytest.raises(PlatformError):
        runtime.compositions.save(actors["owner"], document)


def test_revocation_stops_before_any_target_write_and_redacts_saved_content(composition_case):
    runtime, actors, _, grants = composition_case
    run_id = begin(composition_case)
    runtime.access.revoke(grants[1]["id"], actor_id="manager")
    result = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert result["status"] == "blocked" and not result["content_available"]
    assert result["input"] == {} and all(s["output"] is None for s in result["steps"])
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0


def test_lost_response_is_recovered_from_receipt_without_replaying(composition_case, monkeypatch):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    original = runtime.compositions.execution.finish

    async def crash(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime.compositions.execution, "finish", crash)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 1
    monkeypatch.setattr(runtime.compositions.execution, "finish", original)
    result = asyncio.run(
        runtime.compositions.execution.advance(actors["owner"], run_id, reconcile=True)
    )
    assert result["status"] == "succeeded", result
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 2


def test_interrupted_dispatch_with_no_receipt_does_not_send_again(composition_case):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    snapshot = runtime.compositions.load(actors["owner"], run_id)
    runtime.compositions.transition(
        snapshot,
        snapshot["steps"][0],
        run_status="dispatching",
        status="dispatching",
        dispatch_token="lost-token",
    )
    result = asyncio.run(
        runtime.compositions.execution.advance(actors["owner"], run_id, reconcile=True)
    )
    assert result["status"] == "unknown"
    asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0


@pytest.mark.parametrize(
    "source,target,expected",
    [
        ({"type": "integer"}, {"type": "string"}, False),
        ({"type": "string", "maxLength": 100}, {"type": "string", "maxLength": 50}, False),
        ({"type": "integer", "minimum": 2}, {"type": "number", "minimum": 1}, True),
        ({"type": "string"}, {"type": ["string", "null"]}, False),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "object", "required": ["x"]},
            False,
        ),
    ],
)
def test_connection_type_proof_is_conservative(source, target, expected):
    assert compatible(source, target) == expected


def test_each_confirmation_is_bound_to_its_own_step_and_revision(composition_case):
    runtime, actors, _, _ = composition_case
    for module in ("kit.first", "kit.second"):
        for registry in (runtime.registry, runtime.local_registry):
            registry.capabilities[module + ".analyze"]["execution"]["confirmation"] = True
    run_id = begin(composition_case)
    first = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert first["status"] == "confirming"
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0
    second = asyncio.run(
        runtime.compositions.execution.advance(
            actors["owner"], run_id, revision=first["revision"], confirm_step="first"
        )
    )
    assert second["status"] == "confirming" and second["steps"][0]["status"] == "succeeded"
    with pytest.raises(PlatformError, match="刷新"):
        asyncio.run(
            runtime.compositions.execution.advance(
                actors["owner"], run_id, revision=first["revision"], confirm_step="first"
            )
        )
    completed = asyncio.run(
        runtime.compositions.execution.advance(
            actors["owner"], run_id, revision=second["revision"], confirm_step="second"
        )
    )
    assert completed["status"] == "succeeded"


def test_background_receipt_is_awaited_before_using_its_data(composition_case):
    runtime, actors, _, _ = composition_case
    for registry in (runtime.registry, runtime.local_registry):
        registry.capabilities["kit.first.analyze"]["execution"]["mode"] = "background"
    run_id = begin(composition_case)
    pending = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert pending["status"] == "waiting_run" and pending["steps"][1]["status"] == "pending"
    from app.db import Run

    with runtime.sessions.begin() as db:
        job = db.query(Run).one()
        job.status = "running"
        job_id = job.id
    asyncio.run(runtime.process_run(job_id))
    result = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert result["status"] == "succeeded", result
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 2


def test_source_revocation_after_first_step_blocks_the_second(composition_case, monkeypatch):
    runtime, actors, _, grants = composition_case
    run_id = begin(composition_case)
    finish = runtime.compositions.execution.finish

    async def revoke_after_receipt(actor, snapshot, step, output, **kwargs):
        await finish(actor, snapshot, step, output, **kwargs)
        runtime.access.revoke(grants[0]["id"], actor_id="manager")

    monkeypatch.setattr(runtime.compositions.execution, "finish", revoke_after_receipt)
    result = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert result["status"] == "blocked" and not result["content_available"]
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 1


def test_a_saved_plan_never_upgrades_its_pinned_activation(composition_case):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    runtime.registry.capabilities["kit.second.analyze"]["module_revision"] = 9
    result = asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    assert result["status"] == "blocked"
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0


def test_definition_schema_cannot_fetch_an_external_reference(composition_case):
    runtime, actors, document, _ = composition_case
    document["input_schema"]["properties"]["text"] = {"$ref": "https://invalid.example/schema.json"}
    with pytest.raises(PlatformError, match="本地字段"):
        runtime.compositions.save(actors["owner"], document)


def test_definition_cannot_install_an_arbitrary_validation_pattern(composition_case):
    runtime, actors, document, _ = composition_case
    document["input_schema"]["properties"]["text"]["pattern"] = "(a+)+$"
    with pytest.raises(PlatformError) as caught:
        runtime.compositions.save(actors["owner"], document)
    assert caught.value.code == "composition_schema"


def test_optional_input_binding_can_be_left_unfilled(composition_case):
    runtime, actors, _, _ = composition_case
    definition = {
        "id": "choose",
        "bindings": {"object_id": {"source": "input", "path": ["record"]}},
    }
    snapshot = {
        "input": {},
        "pins": {
            "choose": {
                "flow": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"object_id": {"type": "string"}},
                        "additionalProperties": False,
                    }
                }
            }
        },
    }
    values = asyncio.run(runtime.compositions.inputs(actors["owner"], snapshot, definition))
    assert values == {}
    snapshot["input"] = {"record": None}
    with pytest.raises(PlatformError):
        asyncio.run(runtime.compositions.inputs(actors["owner"], snapshot, definition))


def test_required_target_cannot_depend_on_an_optional_input(composition_case):
    runtime, actors, document, _ = composition_case
    document["input_schema"]["required"] = []
    with pytest.raises(PlatformError, match="必填"):
        runtime.compositions.save(actors["owner"], document)


def test_unsupported_boolean_field_schemas_are_rejected_conservatively():
    assert not compatible(True, {"type": "string"})


def test_dialogue_prepares_input_and_keeps_one_run_reference_when_branched(composition_case):
    from app.conversation import ConversationService

    runtime, actors, document, _ = composition_case
    definition = runtime.compositions.save(actors["owner"], document)
    conversations = ConversationService(runtime)
    conversation = conversations.create(actors["owner"])
    prepared = asyncio.run(
        conversations.send(
            actors["owner"], conversation["id"], "/compose " + definition["id"], None, "guide"
        )
    )
    assert prepared["message"]["views"][0]["type"] == "composition-start"
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0
    run_id = runtime.compositions.start_run(
        actors["owner"], definition["id"], {"text": "hello"}, "chat-start", conversation["id"]
    )
    asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    messages = conversations.get(actors["owner"], conversation["id"])["messages"]
    assert messages[-1]["views"][0] == {"type": "composition", "data": {"run_id": run_id}}
    branch = conversations.fork(actors["owner"], conversation["id"])
    branch_messages = conversations.get(actors["owner"], branch["id"])["messages"]
    assert branch_messages[-1]["views"][0]["data"]["run_id"] == run_id
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 2


def test_worker_continues_a_saved_run_without_an_open_page(composition_case):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)

    async def background():
        await runtime.compositions.start()
        try:
            for _ in range(60):
                result = await runtime.compositions.read(actors["owner"], run_id)
                if result["status"] == "succeeded":
                    return result
                await asyncio.sleep(0.05)
            pytest.fail("The persisted run was not advanced by the coordinator")
        finally:
            await runtime.compositions.stop()

    result = asyncio.run(background())
    assert result["steps"][1]["status"] == "succeeded"


def test_worker_survives_an_unexpected_observation_failure(composition_case, monkeypatch):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    advance = runtime.compositions.execution.advance
    attempts = []

    async def interrupted(actor, identifier):
        attempts.append(identifier)
        if len(attempts) == 1:
            raise RuntimeError("synthetic observation failure")
        return await advance(actor, identifier)

    monkeypatch.setattr(runtime.compositions.execution, "advance", interrupted)

    async def background():
        await runtime.compositions.start()
        try:
            for _ in range(80):
                result = await runtime.compositions.read(actors["owner"], run_id)
                if result["status"] == "succeeded":
                    return
                await asyncio.sleep(0.05)
            pytest.fail("An observation failure stopped the coordinator")
        finally:
            await runtime.compositions.stop()

    asyncio.run(background())
    assert len(attempts) >= 2


def test_current_state_is_bound_to_the_projected_object_type():
    from app.composition_graph import CompositionGraph

    workflows = SimpleNamespace(
        definitions={
            "first.flow": {"provider": {"object_type": "first.record"}},
            "second.flow": {"provider": {"object_type": "second.record"}},
        }
    )
    graph = CompositionGraph(SimpleNamespace(runtime=SimpleNamespace(workflows=workflows)))
    first = {"scope": "business", "owner_id": "first.flow"}
    second = {"scope": "business", "owner_id": "second.flow"}
    flow = {"entry": {"id": "first.read"}, "data_objects": [{"id": "first.record"}]}
    assert graph.machines({"state_machines": [first, second]}, flow) == [first]
    projection = {"object_ref": {"object_type": "first.record"}, "state": "draft"}
    assert graph.matches_object(first, projection)
    assert not graph.matches_object(second, projection)


def test_runtime_graph_namespaces_repeated_nodes_and_has_real_data_edges(composition_case):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    graph = asyncio.run(runtime.compositions.graph.read(actors["owner"], run_id))
    identifiers = {node["id"] for node in graph["nodes"]}
    assert len(identifiers) == len(graph["nodes"])
    assert all(edge["from"] in identifiers and edge["to"] in identifiers for edge in graph["edges"])
    assert any(
        edge["from"] == "first::output:normalized_text" and edge["to"] == "second::input:text"
        for edge in graph["edges"]
    )
    assert any(
        node.get("activity") == "succeeded" and node["kind"] == "business"
        for node in graph["nodes"]
    )
    assert {item["freshness"] for item in graph["observations"]} == {"receipt"}


def test_role_groups_are_atomic_scoped_and_repeatable(composition_case):
    from app.access_control import AccessGrant

    runtime, _, _, _ = composition_case
    bindings = [
        {"module_id": module, "role_id": module + ".user", "scope": {"organization_id": "org-a"}}
        for module in ("kit.first", "kit.second")
    ]
    wrong = deepcopy(bindings)
    wrong[1]["scope"] = {"unknown_scope": "org-a"}
    with pytest.raises(PlatformError):
        runtime.access.grant_many("reviewer", wrong, actor_id="manager")
    with runtime.sessions() as db:
        assert db.query(AccessGrant).filter_by(subject_id="reviewer").count() == 0
    granted = runtime.access.grant_many("reviewer", bindings, actor_id="manager")
    repeated = runtime.access.grant_many("reviewer", bindings, actor_id="manager")
    assert granted == repeated
    assert len(granted["items"]) == 2
    assert all(item["scope"] == {"organization_id": "org-a"} for item in granted["items"])
    with pytest.raises(PlatformError):
        runtime.access.grant_many("outside", bindings, actor_id="owner")


def test_runtime_graph_hides_module_details_after_permission_change(composition_case):
    runtime, actors, _, grants = composition_case
    run_id = begin(composition_case)
    asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    runtime.access.revoke(grants[0]["id"], actor_id="manager")
    graph = asyncio.run(runtime.compositions.graph.read(actors["owner"], run_id))
    assert not graph["content_available"]
    assert all(node["kind"] == "entry" for node in graph["nodes"])
    assert not graph["observations"]


def test_published_composition_requires_each_modules_grants_and_keeps_runs_private(
    composition_case,
):
    runtime, actors, document, _ = composition_case
    bindings = [
        {"module_id": module, "role_id": module + ".user", "scope": {"organization_id": "org-a"}}
        for module in ("kit.first", "kit.second")
    ]
    runtime.access.grant_many("manager", bindings, actor_id="manager")
    document["roles"] = [{"id": "operator", "title": "办理人", "bindings": bindings}]
    saved = runtime.compositions.save(actors["manager"], document)
    assert not runtime.compositions.definitions(actors["reviewer"])["items"]
    runtime.compositions.publish(actors["manager"], saved["id"], True)
    assert not runtime.compositions.definitions(actors["reviewer"])["items"]
    runtime.compositions.grant_role(actors["manager"], saved["id"], "operator", "reviewer")
    visible = runtime.compositions.definitions(actors["reviewer"])["items"]
    assert [item["id"] for item in visible] == [saved["id"]]
    run_id = runtime.compositions.start_run(
        actors["reviewer"], saved["id"], {"text": "reviewer input"}, "reviewer-run"
    )
    result = asyncio.run(runtime.compositions.execution.advance(actors["reviewer"], run_id))
    assert result["status"] == "succeeded", result
    with pytest.raises(PlatformError):
        asyncio.run(runtime.compositions.read(actors["manager"], run_id))
    runtime.compositions.publish(actors["manager"], saved["id"], False)
    assert not runtime.compositions.definitions(actors["reviewer"])["items"]
    # Removing a catalog entry does not delete the user's completed receipt.
    assert (
        asyncio.run(runtime.compositions.read(actors["reviewer"], run_id))["status"] == "succeeded"
    )


def test_interrupted_command_receipt_preserves_uncertainty_without_resend(
    composition_case, monkeypatch
):
    runtime, actors, _, _ = composition_case
    run_id = begin(composition_case)
    snapshot = runtime.compositions.load(actors["owner"], run_id)
    runtime.compositions.transition(
        snapshot,
        snapshot["steps"][0],
        run_status="waiting_run",
        status="waiting_run",
        dispatch_token="accepted-command",
    )
    # Reproduce the persisted receipt returned by Runtime.start after a real host
    # interruption. The separate HTTP acceptance verifies an actual remote commit.
    monkeypatch.setattr(
        runtime.compositions.execution,
        "receipt",
        lambda *args: {
            "id": "accepted-command",
            "status": "failed",
            "result": None,
            "error": {"code": "interrupted", "message": "interrupted"},
        },
    )
    result = asyncio.run(
        runtime.compositions.execution.advance(actors["owner"], run_id, reconcile=True)
    )
    assert result["status"] == "unknown"
    assert result["steps"][0]["status"] == "unknown"
    assert result["error"]["code"] == "outcome_unknown"
    asyncio.run(runtime.compositions.execution.advance(actors["owner"], run_id))
    with runtime.sessions() as db:
        assert db.query(Invocation).count() == 0
