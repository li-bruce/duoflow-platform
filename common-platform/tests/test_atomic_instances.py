"""Atomic instance links use real shared-host ownership, revisions and grants."""

import asyncio
import secrets
from types import SimpleNamespace

import pytest

from app.atomic_instances import PresenceInput
from app.errors import PlatformError
from app.runtime import Runtime
from app.conversation import ConversationService
from app.module_catalog import OpenBusiness
from app.db import Run, WorkflowInstance
from app.remote_services import RemoteServiceManager
from test_module_kit import generated, platform_root, write_json, kit, bridge_http, start_node


@pytest.fixture
def atomic_case(tmp_path):
    package = generated(tmp_path)
    base = tmp_path / "host"
    engine, sessions, actors = platform_root(base, package)
    runtime = Runtime(
        base,
        sessions,
        SimpleNamespace(configured=False, redact=lambda value: value),
        data_dir=base / "data",
    )
    grant = runtime.access.grant(
        "owner", "kit.text", "kit.text.user", {"organization_id": "org-a"}, actor_id="manager"
    )
    item = runtime.interactions.create(actors["owner"], "kit.text.tool", {}, "open")
    yield runtime, actors, item, grant
    engine.dispose()


def test_local_presence_and_saved_drafts_are_distinct_from_business_state(atomic_case):
    runtime, actors, item, _ = atomic_case
    service, actor = runtime.atomic_instances, actors["owner"]
    body = PresenceInput(
        session_id="a" * 32, surface="conversation", status="editing", revision=item["revision"]
    )
    assert asyncio.run(service.report(actor, "interaction", item["id"], body))["registered"]
    view = asyncio.run(service.read(actor, "interaction", item["id"]))
    assert view["business_state"] is None
    atom = view["instances"][0]
    assert atom["nodes"] and atom["local_state"] == item["state"]
    assert atom["presence"][0]["status"] == "editing" and atom["presence"][0]["fresh"]
    runtime.observations.clock = lambda: atom["presence"][0]["received_at"] + 16
    assert not asyncio.run(service.read(actor, "interaction", item["id"]))["instances"][0][
        "presence"
    ][0]["fresh"]


def test_presence_cannot_switch_owner_host_or_revision(atomic_case):
    runtime, actors, item, _ = atomic_case
    service = runtime.atomic_instances
    body = PresenceInput(
        session_id="a" * 32, surface="page", status="ready", revision=item["revision"]
    )
    for actor, changed in [
        (actors["outside"], body),
        (actors["owner"], body.model_copy(update={"step_id": "invented"})),
        (actors["owner"], body.model_copy(update={"revision": 99})),
    ]:
        with pytest.raises(PlatformError):
            asyncio.run(service.report(actor, "interaction", item["id"], changed))
    assert not asyncio.run(service.read(actors["owner"], "interaction", item["id"]))["instances"][
        0
    ]["presence"]


def test_revocation_removes_access_to_both_graph_and_instance(atomic_case):
    runtime, actors, item, grant = atomic_case
    runtime.access.revoke(grant["id"], actor_id="manager")
    with pytest.raises(PlatformError):
        asyncio.run(runtime.atomic_instances.read(actors["owner"], "interaction", item["id"]))
    with pytest.raises(PlatformError):
        runtime.module_catalog.module(actors["owner"], "kit.text")


def test_interaction_heartbeats_do_not_refresh_stored_action_results(atomic_case, monkeypatch):
    runtime, actors, item, _ = atomic_case
    def no_result_refresh(*args, **kwargs):
        raise AssertionError("Presence refreshed an action result")
    monkeypatch.setattr(runtime.interactions, "get", no_result_refresh)
    body = PresenceInput(session_id="e"*32, surface="page", status="ready", revision=item["revision"])
    assert asyncio.run(runtime.atomic_instances.report(actors["owner"], "interaction", item["id"], body))["registered"]
    with pytest.raises(AssertionError, match="action result"):
        asyncio.run(runtime.atomic_instances.read(actors["owner"], "interaction", item["id"]))


def test_public_business_dialogue_opens_the_same_atomic_component(atomic_case):
    runtime, actors, _, _ = atomic_case
    service = ConversationService(runtime)
    conversation = service.create(actors["owner"])
    response = asyncio.run(
        service.send(
            actors["owner"],
            conversation["id"],
            "/business kit.text.analyze.public",
            None,
            "guided-entry",
        )
    )
    view = response["message"]["views"][0]
    assert view["type"] == "interaction"
    item = runtime.interactions.get(actors["owner"], view["data"]["instance_id"])
    assert item["component_id"] == "kit.text.tool" and item["result"] is None
    atomic = asyncio.run(runtime.atomic_instances.read(actors["owner"], "interaction", item["id"]))
    assert atomic["registered"] and atomic["instances"][0]["nodes"]


def test_module_names_do_not_claim_other_modules_components(atomic_case):
    runtime, actors, _, _ = atomic_case
    runtime.interactions.definitions["kit.text.extra.tool"] = {
        "id": "kit.text.extra.tool",
        "actions": [{"capability_id": "kit.text.extra.analyze"}],
    }
    module = runtime.module_catalog.module(actors["owner"], "kit.text")
    assert module["flows"][0]["id"] == "kit.text.analyze.public"


def test_module_page_opens_owned_component_and_reuses_retry_key(atomic_case):
    runtime, actors, _, grant = atomic_case
    body = OpenBusiness(version="1.0.0", idempotency_key="module-page")

    async def open_entry():
        return await runtime.module_catalog.open(
            actors["owner"], "kit.text", "kit.text.analyze.public", body
        )

    result = asyncio.run(open_entry())
    assert result["view"]["type"] == "interaction"
    assert result == asyncio.run(open_entry())
    instance = runtime.interactions.get(actors["owner"], result["view"]["data"]["instance_id"])
    assert instance["result"] is None
    runtime.access.revoke(grant["id"], actor_id="manager")
    with pytest.raises(PlatformError):
        asyncio.run(open_entry())


@pytest.fixture
def remote_presence_case(tmp_path):
    package = generated(tmp_path, "workflow-node", "kit.presence")
    write_json(package / "backend/projects.example.json", [
        {"project_id": "P", "organization_id": "org-a", "title": "Synthetic"}])
    base = tmp_path / "host"
    engine, sessions, actors = platform_root(base, package, "workflow")
    manager = RemoteServiceManager(base / "data/remote")
    runtime = Runtime(base, sessions, SimpleNamespace(configured=False, redact=lambda value: value),
                      manager, base / "data")
    process = None
    try:
        with bridge_http(runtime, sessions) as callback:
            token = secrets.token_urlsafe(32)
            process, url = start_node(package, tmp_path / "records", callback, token)
            manager.save_connection({"id": "presence", "name": "Synthetic", "base_url": url,
                                     "token": token, "timeout_seconds": 10})
            discovered = asyncio.run(manager.discover("presence"))
            runtime.access.register_definition(kit.read_json(package, "backend/access-definition.json"), actor_id="manager")
            admitted = asyncio.run(manager.admit("presence", discovered["revision"],
                host_check=lambda value: runtime.check_remote_registration("presence", value)))
            assert admitted["admission"]["passed"]
            runtime.set_remote_publication("presence", True, discovered["revision"])
            grant = runtime.access.grant("owner", "kit.presence", "kit.presence.author",
                {"project_id": "P", "organization_id": "org-a"}, actor_id="manager")
            item = asyncio.run(runtime.workflows.create(actors["owner"], "kit.presence.workflow", "open"))
            yield runtime, actors, item, grant
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        engine.dispose()


def test_workflow_heartbeats_never_read_remote_business_or_adopt_completed_run(remote_presence_case, monkeypatch):
    runtime, actors, item, _ = remote_presence_case
    actor = actors["owner"]
    receipt = asyncio.run(runtime.workflows.action(actor, item["id"], "create",
        {"project_id": "P", "title": "Synthetic", "body": "Real remote commit"},
        item["revision"], None, "create-once"))
    asyncio.run(runtime.process_run(receipt["instance"]["run_id"]))
    with runtime.sessions() as db:
        run = db.get(Run, receipt["instance"]["run_id"])
        assert run.status == "succeeded", run.error
        assert run.result["data"]["projection"]["state"] == "draft"
        row = db.get(WorkflowInstance, item["id"])
        before = (row.revision, row.object_ref, row.cursor, row.drafts, row.run_id)
    async def no_business_read(*args, **kwargs):
        raise AssertionError("Presence attempted remote business IO")
    monkeypatch.setattr(runtime.workflows.transport, "request", no_business_read)
    def no_business_catalog(*args, **kwargs):
        raise AssertionError("Presence enumerated unrelated business entries")
    monkeypatch.setattr(runtime.module_catalog, "module", no_business_catalog)
    body = PresenceInput(session_id="c"*32, step_id=item["cursor"], surface="page",
                         status="busy", revision=before[0])
    for _ in range(6):
        assert asyncio.run(runtime.atomic_instances.report(actor, "workflow", item["id"], body))["registered"]
    with runtime.sessions() as db:
        row = db.get(WorkflowInstance, item["id"])
        assert (row.revision, row.object_ref, row.cursor, row.drafts, row.run_id) == before
    # Explicit graph inspection must still use the authorized business read.
    with pytest.raises(AssertionError, match="remote business IO"):
        asyncio.run(runtime.atomic_instances.read(actor, "workflow", item["id"]))


def test_metadata_only_workflow_presence_retains_owner_revision_step_and_revocation_checks(remote_presence_case, monkeypatch):
    runtime, actors, item, grant = remote_presence_case
    async def no_business_read(*args, **kwargs):
        raise AssertionError("Presence attempted remote business IO")
    monkeypatch.setattr(runtime.workflows.transport, "request", no_business_read)
    body = PresenceInput(session_id="d"*32, step_id=item["cursor"], surface="conversation",
                         status="ready", revision=item["revision"])
    for actor, changed in [(actors["outside"], body),
                           (actors["owner"], body.model_copy(update={"revision": 99})),
                           (actors["owner"], body.model_copy(update={"step_id": "invented"}))]:
        with pytest.raises(PlatformError):
            asyncio.run(runtime.atomic_instances.report(actor, "workflow", item["id"], changed))
    runtime.access.revoke(grant["id"], actor_id="manager")
    with pytest.raises(PlatformError):
        asyncio.run(runtime.atomic_instances.report(actors["owner"], "workflow", item["id"], body))
    with runtime.observations.database() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_presence").fetchone()[0] == 0
