"""Atomic instance links use real shared-host ownership, revisions and grants."""

import asyncio
from types import SimpleNamespace

import pytest

from app.atomic_instances import PresenceInput
from app.errors import PlatformError
from app.runtime import Runtime
from app.conversation import ConversationService
from app.module_catalog import OpenBusiness
from test_module_kit import generated, platform_root


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
