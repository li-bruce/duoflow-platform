"""Independent-center admission, approvals, durable fan-out and concurrency acceptance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from coordination_service.main import create_app
from coordination_service.protocol import LEASE_SECONDS, CoordinationError, validate_summary, validate_configuration
from pydantic import ValidationError


CONTROL = "control-synthetic-token-000000000000"
TOKENS = {name: "synthetic-module-token-" + name + "-00000000" for name in ("source", "mining", "research", "submission")}
SCOPE = {"organization_id": "org-1", "project_id": "project-1"}
PREFIX = "/api/v1/coordination"


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


def auth(name="control"):
    return {"Authorization": "Bearer " + (CONTROL if name == "control" else TOKENS[name])}


def config():
    kinds = ["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]
    modules = []
    for name in TOKENS:
        modules.append({"id": name, "token": TOKENS[name], "enabled": True,
                        "machines": [{"id": name + ".machine", "object_types": [name + ".item"],
                                      "states": ["draft", "pending", "public", "withdrawn"],
                                      "public_states": ["public"], "approval_required": name == "source"}],
                        "emits": kinds, "accepts": [{"source_module": "source", "kind": kind, "mode": mode}
                        for kind in ("state.changed", "resource.published", "resource.withdrawn") for mode in ("notify", "sync")]})
    return {"expected_revision": 0, "modules": modules,
            "subscriptions": [{"id": "to-" + target, "source_module": "source", "target_module": target,
                               "kind": "state.changed", "scope": deepcopy(SCOPE), "mode": "notify"}
                              for target in ("mining", "research", "submission")]}


def event(identifier="e1", revision=1, state="draft", kind="state.changed", object_id="object-1", module="source", scope=None, approval_ref=None):
    value = {"version": "1.0", "event_id": identifier, "module_id": module, "kind": kind,
             "machine_id": module + ".machine", "object_ref": {"object_type": module + ".item", "object_id": object_id},
             "state": state, "revision": revision, "scope": scope or deepcopy(SCOPE)}
    if approval_ref:
        value["approval_ref"] = approval_ref
    return value


@pytest.fixture
def center(tmp_path):
    clock = Clock()
    app = create_app(tmp_path / "center", start_worker=False, clock=clock, control_token=CONTROL)
    with TestClient(app) as client:
        assert client.put(PREFIX + "/config", json=config(), headers=auth()).status_code == 200
        yield client, app.state.coordination, clock, tmp_path / "center"


def send(client, value, module="source"):
    return client.post(PREFIX + "/events", json=value, headers=auth(module))


def pull(client, module="mining", consumer="worker-1", limit=20):
    return client.get(PREFIX + "/deliveries", params={"consumer_id": consumer, "limit": limit}, headers=auth(module))


def acknowledge(client, delivery, module="mining", **updates):
    command = {"consumer_id": delivery["consumer_id"], "lease_token": delivery["lease_token"], **updates}
    return client.post(PREFIX + "/deliveries/" + delivery["id"] + "/ack", json=command, headers=auth(module))


def summary(client):
    return client.get(PREFIX + "/summary", headers=auth()).json()


def decide(client, identifier="request-1", **updates):
    return client.post(PREFIX + "/approvals/" + identifier + "/decide",
                       json={"expected_revision": 1, "decision": "approved", "actor_id": "real-admin-1", **updates}, headers=auth())


def test_health_and_control_are_separate(center):
    client, _, _, _ = center
    assert client.get("/health").json()["service"] == "duoflow-coordination"
    assert client.get(PREFIX + "/summary", headers=auth("source")).status_code == 401
    assert send(client, event(), "control").status_code == 401
    for header in ("bearer " + CONTROL, "Bearer " + CONTROL + " ", "Bearer  " + CONTROL, "Bearer " + CONTROL + " extra"):
        assert client.get(PREFIX + "/summary", headers={"Authorization": header}).status_code == 401


def test_config_cas_and_atomic_validation(center):
    client, _, _, _ = center
    assert client.put(PREFIX + "/config", json=config(), headers=auth()).status_code == 409
    invalid = config()
    invalid["expected_revision"] = 1
    invalid["modules"][0]["machines"][0]["object_types"] = ["mining.item"]
    assert client.put(PREFIX + "/config", json=invalid, headers=auth()).status_code == 422
    assert summary(client)["config_revision"] == 1
    assert send(client, event()).status_code == 200


@pytest.mark.parametrize("change", ["duplicate_token", "wildcard_scope", "undeclared_emit", "undeclared_accept", "private_request_subscription"])
def test_config_rejects_unsafe_subscriptions(center, change):
    client, _, _, _ = center
    invalid = config()
    invalid["expected_revision"] = 1
    if change == "duplicate_token":
        invalid["modules"][1]["token"] = invalid["modules"][0]["token"]
    if change == "wildcard_scope":
        invalid["subscriptions"][0]["scope"]["project_id"] = "*"
    if change == "undeclared_emit":
        invalid["modules"][0]["emits"] = []
    if change == "undeclared_accept":
        invalid["modules"][1]["accepts"] = []
    if change == "private_request_subscription":
        invalid["subscriptions"][0]["kind"] = "publication.requested"
        invalid["modules"][1]["accepts"].append({"source_module": "source", "kind": "publication.requested", "mode": "notify"})
    assert client.put(PREFIX + "/config", json=invalid, headers=auth()).status_code == 422
    assert summary(client)["config_revision"] == 1


def test_duplicate_and_conflicting_event_ids(center):
    client, _, _, _ = center
    assert send(client, event()).json()["duplicate"] is False
    assert send(client, event()).json()["duplicate"] is True
    assert send(client, event(state="pending")).status_code == 409
    assert summary(client)["counts"]["events"] == 1
    assert summary(client)["counts"]["deliveries_pending"] == 3


@pytest.mark.parametrize("field,value", [("module_id", "mining"), ("machine_id", "mining.machine"), ("state", "invented")])
def test_forgery_and_unknown_declarations(center, field, value):
    client, _, _, _ = center
    invalid = event()
    invalid[field] = value
    assert send(client, invalid).status_code in (403, 422)
    assert summary(client)["counts"]["events"] == 0


def test_exact_body_and_size_limits(center):
    client, _, _, _ = center
    invalid = {**event(), "payload": "business-document"}
    response = send(client, invalid)
    assert response.status_code == 422 and "business-document" not in response.text
    assert client.post(PREFIX + "/events", content=b" " * 8193, headers=auth("source")).status_code == 413
    raw = json.dumps(event()).replace('"version": "1.0"', '"version": "1.0", "version": "1.0"')
    assert client.post(PREFIX + "/events", content=raw, headers=auth("source")).status_code == 422


def test_revision_order_and_scope_conflict(center):
    client, _, _, _ = center
    assert send(client, event(revision=4)).status_code == 200
    assert send(client, event("e2", revision=3)).status_code == 409
    assert send(client, event("e2", revision=4, state="pending")).status_code == 409
    assert send(client, event("e2", revision=4, scope={**SCOPE, "project_id": "other"})).status_code == 409
    assert summary(client)["objects"][0]["revision"] == 4


def test_scope_fanout_and_four_parallel_modules(center):
    client, _, _, _ = center
    assert send(client, event()).status_code == 200
    for module in ("mining", "research", "submission"):
        assert send(client, event(module + "-own", module=module), module).status_code == 200
        values = pull(client, module).json()["deliveries"]
        assert len(values) == 1 and values[0]["target_module"] == module
    assert send(client, event("outside", object_id="other", scope={**SCOPE, "project_id": "other"})).status_code == 200
    assert summary(client)["counts"]["objects"] == 5
    assert summary(client)["counts"]["deliveries_leased"] == 3
    assert summary(client)["counts"]["deliveries_pending"] == 0


def test_same_object_order_and_different_object_parallel(center):
    client, _, _, _ = center
    for value in (event(), event("e2", revision=2, state="pending"), event("other", object_id="other")):
        assert send(client, value).status_code == 200
    first = pull(client).json()["deliveries"]
    assert [d["event"]["event_id"] for d in first] == ["e1", "other"]
    assert pull(client, consumer="worker-2").json()["deliveries"] == []
    assert acknowledge(client, first[0]).status_code == 200
    assert pull(client, consumer="worker-2").json()["deliveries"][0]["event"]["event_id"] == "e2"


def test_consumer_crash_expiry_and_old_receipt(center):
    client, _, clock, _ = center
    send(client, event())
    old = pull(client).json()["deliveries"][0]
    assert acknowledge(client, old, consumer_id="forged-worker").status_code == 409
    assert acknowledge(client, old, module="research").status_code == 404
    clock.value += LEASE_SECONDS + 1
    assert acknowledge(client, old).status_code == 409
    new = pull(client, consumer="worker-2").json()["deliveries"][0]
    assert old["id"] == new["id"] and new["attempts"] == 2
    assert old["lease_token"] != new["lease_token"]
    assert acknowledge(client, old).status_code == 409
    assert acknowledge(client, new).json()["duplicate"] is False
    assert acknowledge(client, new).json()["duplicate"] is True


def test_center_restart_preserves_deduplication_and_leases(center):
    client, _, clock, directory = center
    send(client, event())
    old = pull(client).json()["deliveries"][0]
    restarted = create_app(directory, start_worker=False, clock=clock, control_token=CONTROL)
    with TestClient(restarted) as second:
        assert send(second, event()).json()["duplicate"] is True
        assert pull(second).json()["deliveries"] == []
        assert acknowledge(second, old).status_code == 200
        assert summary(second)["counts"]["events"] == 1
        assert len(pull(second, "research").json()["deliveries"]) == 1


def test_parallel_claims_do_not_share_a_lease(center):
    client, store, _, _ = center
    for i in range(10):
        send(client, event("parallel-" + str(i), object_id="o-" + str(i)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda n: store.pull(TOKENS["mining"], "w-" + str(n), 3), range(4)))
    identifiers = [d["id"] for response in responses for d in response["deliveries"]]
    assert len(identifiers) == len(set(identifiers)) == 10


def test_request_private_decision_to_source_and_approved_publication(center):
    client, _, _, _ = center
    command = config()
    command["expected_revision"] = 1
    for sub in command["subscriptions"]:
        sub["kind"] = "resource.published"
        sub["mode"] = "sync"
    client.put(PREFIX + "/config", json=command, headers=auth())
    request = event("request-1", revision=4, state="pending", kind="publication.requested")
    assert send(client, request).status_code == 200
    assert summary(client)["counts"]["approvals_pending"] == 1
    assert pull(client).json()["deliveries"] == []
    assert client.post(PREFIX + "/approvals/request-1/decide", json={"expected_revision": 1, "decision": "approved", "actor_id": "fake-admin"}, headers=auth("source")).status_code == 401
    result = decide(client)
    assert result.status_code == 200 and result.json()["approval"]["revision"] == 2
    source = pull(client, "source").json()["deliveries"][0]
    assert source["event"]["kind"] == "publication.approved" and source["event"]["approval_ref"] == "request-1"
    assert source["event"]["revision"] == 4
    acknowledge(client, source, "source")
    published = event("published-1", revision=5, state="public", kind="resource.published", approval_ref="request-1")
    assert send(client, published).status_code == 200
    assert send(client, published).json()["duplicate"] is True
    for module in ("mining", "research", "submission"):
        delivery = pull(client, module).json()["deliveries"][0]
        assert delivery["mode"] == "sync" and delivery["event"] == published
    assert summary(client)["approvals"][0]["used_by"] == "published-1"


@pytest.mark.parametrize("kind", ["resource.published", "state.changed"])
def test_unapproved_publication_cannot_bypass_by_kind(center, kind):
    client, _, _, _ = center
    response = send(client, event(state="public", kind=kind))
    assert response.status_code == 403
    assert summary(client)["counts"]["events"] == 0


def test_rejection_and_decision_cas(center):
    client, _, _, _ = center
    send(client, event("request-1", state="pending", kind="publication.requested"))
    assert decide(client, decision="rejected").status_code == 200
    assert decide(client).status_code == 409
    rejected = pull(client, "source").json()["deliveries"][0]
    assert rejected["event"]["kind"] == "publication.rejected"
    assert send(client, event("bad", revision=2, state="public", kind="resource.published", approval_ref="request-1")).status_code == 403


@pytest.mark.parametrize("mutation", ["revision", "scope", "object"])
def test_approval_binds_exact_object_scope_and_next_revision(center, mutation):
    client, _, _, _ = center
    send(client, event("request-1", revision=4, state="pending", kind="publication.requested"))
    assert decide(client).status_code == 200
    value = event("publication", revision=5, state="public", kind="resource.published", approval_ref="request-1")
    if mutation == "revision":
        value["revision"] = 6
    if mutation == "scope":
        value["scope"] = {**SCOPE, "project_id": "other"}
    if mutation == "object":
        value["object_ref"]["object_id"] = "other"
    assert send(client, value).status_code == 409
    assert summary(client)["approvals"][0]["used_by"] is None


def test_stale_request_cannot_be_decided_or_published(center):
    client, _, _, _ = center
    send(client, event("request-1", revision=4, state="pending", kind="publication.requested"))
    send(client, event("edit", revision=5, state="draft"))
    assert decide(client).status_code == 409
    send(client, event("request-2", revision=6, state="pending", kind="publication.requested"))
    assert decide(client, "request-2").status_code == 200
    send(client, event("edit2", revision=7, state="draft"))
    assert send(client, event("pub", revision=8, state="public", kind="resource.published", approval_ref="request-2")).status_code == 409


def test_approval_cannot_be_reused_for_another_event(center):
    client, _, _, _ = center
    send(client, event("request-1", state="pending", kind="publication.requested"))
    decide(client)
    value = event("pub1", revision=2, state="public", kind="resource.published", approval_ref="request-1")
    assert send(client, value).status_code == 200
    value["event_id"] = "pub2"
    assert send(client, value).status_code == 403


@pytest.mark.parametrize("change", ["remove_subscription", "disable_source", "disable_target", "change_scope"])
def test_configuration_revocation_cancels_pending_and_leased(center, change):
    client, _, _, _ = center
    send(client, event())
    leased = pull(client).json()["deliveries"][0]
    command = config()
    command["expected_revision"] = 1
    if change == "remove_subscription":
        command["subscriptions"] = command["subscriptions"][1:]
    if change == "disable_source":
        command["modules"][0]["enabled"] = False
    if change == "disable_target":
        command["modules"][1]["enabled"] = False
    if change == "change_scope":
        command["subscriptions"][0]["scope"] = {**SCOPE, "project_id": "other"}
    assert client.put(PREFIX + "/config", json=command, headers=auth()).status_code == 200
    response = pull(client)
    assert response.status_code == 403 if change == "disable_target" else response.json()["deliveries"] == []
    assert acknowledge(client, leased).status_code in (403, 409)


def test_token_rotation_is_immediate_and_tokens_are_only_hashed(center):
    client, store, _, directory = center
    command = config()
    command["expected_revision"] = 1
    rotated = "rotated-synthetic-module-token-000000"
    command["modules"][0]["token"] = rotated
    assert client.put(PREFIX + "/config", json=command, headers=auth()).status_code == 200
    assert send(client, event()).status_code == 401
    assert client.post(PREFIX + "/events", json=event(), headers={"Authorization": "Bearer " + rotated}).status_code == 200
    with store.database() as db:
        dump = "\n".join(db.iterdump())
    assert CONTROL not in dump and rotated not in dump
    assert all(token not in dump for token in TOKENS.values())


def test_summary_bounds_and_metadata_only(center):
    client, _, clock, _ = center
    for i in range(105):
        clock.value += 1
        assert send(client, event("req-" + str(i), object_id="o-" + str(i), state="pending", kind="publication.requested")).status_code == 200
    value = summary(client)
    assert len(value["objects"]) == len(value["approvals"]) == 100
    assert value["counts"]["objects"] == value["counts"]["approvals_pending"] == 105
    serialized = json.dumps(value)
    assert all(token not in serialized for token in [CONTROL, *TOKENS.values()])
    assert not any(word in serialized for word in ("token_hash", "lease_token", '"payload"', '"declaration"'))


def test_declaration_removal_filters_summary(center):
    client, _, _, _ = center
    send(client, event())
    command = config()
    command["expected_revision"] = 1
    command["modules"][0]["machines"] = []
    assert client.put(PREFIX + "/config", json=command, headers=auth()).status_code == 200
    assert summary(client)["objects"] == []
    assert pull(client).json()["deliveries"] == []


def test_limit_and_consumer_validation(center):
    client, _, _, _ = center
    for consumer, limit in (("", 20), ("*", 20), ("worker", 21), ("worker", 0)):
        assert pull(client, consumer=consumer, limit=limit).status_code == 422


def test_fanout_failure_rolls_back_event_state_and_all_deliveries(center, monkeypatch):
    client, store, _, _ = center
    original, count = store._enqueue, 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("synthetic crash during center transaction")
        return original(*args)

    monkeypatch.setattr(store, "_enqueue", fail_second)
    with pytest.raises(RuntimeError):
        store.accept(TOKENS["source"], event())
    value = summary(client)
    assert value["counts"]["events"] == value["counts"]["objects"] == value["counts"]["deliveries_pending"] == 0
    monkeypatch.setattr(store, "_enqueue", original)
    assert send(client, event()).status_code == 200


def test_concurrent_publication_uses_approval_once(center):
    client, store, _, _ = center
    send(client, event("request-1", state="pending", kind="publication.requested"))
    decide(client)

    def publish(number):
        try:
            return store.accept(TOKENS["source"], event("pub-" + str(number), revision=2, state="public", kind="resource.published", approval_ref="request-1"))["accepted"]
        except CoordinationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, range(2))) == [False, True]


@pytest.mark.parametrize("mutation", ["payload", "too_many_objects", "invalid_approval", "credential_field"])
def test_main_api_summary_revalidation(center, mutation):
    client, _, _, _ = center
    send(client, event("request-1", state="pending", kind="publication.requested"))
    value = summary(client)
    assert validate_summary(value) == value
    if mutation == "payload":
        value["objects"][0]["payload"] = "private document"
    if mutation == "too_many_objects":
        value["objects"] *= 101
    if mutation == "invalid_approval":
        value["approvals"][0]["status"] = "approved"
    if mutation == "credential_field":
        value["backlogs"][0]["token"] = "synthetic-secret"
    with pytest.raises(ValidationError):
        validate_summary(value)


def test_removed_approval_machine_hides_and_cancels_source_message(center):
    client, _, _, _ = center
    send(client, event("request-1", state="pending", kind="publication.requested"))
    decide(client)
    command = config()
    command["expected_revision"] = 1
    command["modules"][0]["machines"] = []
    assert client.put(PREFIX + "/config", json=command, headers=auth()).status_code == 200
    assert summary(client)["approvals"] == []
    assert pull(client, "source").json()["deliveries"] == []


def test_control_config_read_is_current_and_contains_no_credentials(center):
    client, _, _, _ = center
    response = client.get(PREFIX + "/config", headers=auth())
    value = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert validate_configuration(value) == value
    assert value["config_revision"] == 1 and len(value["modules"]) == 4
    assert all("token" not in m and "token_hash" not in m for m in value["modules"])
    assert all(token not in response.text for token in [CONTROL, *TOKENS.values()])
    assert client.get(PREFIX + "/config", headers=auth("source")).status_code == 401
