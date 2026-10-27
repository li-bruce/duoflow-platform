"""Current consumer/source scopes and fixed versions over the read-only seam."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.errors import PlatformError, require
from app.source_reads import SourceReads, create_source_read_router
from test_access_bridge import bridge_case, ticket
from test_access_control import definition


@pytest.fixture
def source_case(bridge_case, tmp_path):
    case = bridge_case
    case.runtime.access_bridge = case.bridge
    access = case.runtime.access
    access.register_definition(definition("source.library"), actor_id="manager")
    grant = access.grant(
        "alice", "source.library", "source.library.submitter",
        {"project_id": "P", "owner_id": "alice"}, actor_id="manager",
    )
    source_spec = {
        "id": "source.library.read", "version": "1.0", "module_id": "source.library",
        "service_id": "source-http", "binding_revision": 1, "module_revision": 1, "kind": "query",
    }
    case.runtime.registry.capabilities[source_spec["id"]] = source_spec
    storage = tmp_path / "source.sqlite"
    with sqlite3.connect(storage) as db:
        db.execute("create table snapshot (id text, revision integer, project text, owner text, description text)")
        db.execute("insert into snapshot values ('fixed_1', 3, 'P', 'alice', 'Synthetic published specimen')")
    calls = []

    def module(actor, module_id):
        require(module_id == "source.library", "module_missing", "Unknown module", 404)
        return {"registration": {"data_objects": [
            {"id": "source.library.snapshot", "operations": {"read": source_spec["id"]}}
        ]}}

    async def read(actor, capability_id, operation, payload, *, source_chain=()):
        # The real source bridge mints and verifies an independently bound ticket.
        source_ticket = case.bridge.issue(actor, source_spec, "source-request", operation, source_chain=source_chain)
        calls.append({"ticket": source_ticket, "payload": payload, "chain": source_chain})
        with sqlite3.connect(storage) as db:
            row = db.execute("select * from snapshot where id = ?", (payload["object_ref"]["object_id"],)).fetchone()
        require(row is not None, "not_found", "Missing source", 404)
        decision = case.bridge.decide(
            source_ticket, "source-request", "source.library.read",
            {"project_id": row[2], "owner_id": row[3]}, operation="objects.read",
        )
        require(decision["allowed"], decision["reason"], "Source denied", 403)
        if getattr(case, "after_source", None):
            case.after_source()
        return {"projection": {
            "object_ref": payload["object_ref"], "revision": row[1], "state": "published",
            "data": {"description": row[4]}, "allowed_actions": [],
        }}

    case.runtime.module_catalog = SimpleNamespace(module=module)
    case.runtime.workflows = SimpleNamespace(transport=SimpleNamespace(request=read))
    case.service = SourceReads(case.runtime)
    case.client.app.include_router(create_source_read_router(case.service))
    case.calls, case.source_grant, case.storage = calls, grant, storage
    case.source_spec = source_spec
    return case


def body(**changes):
    return {
        "source_read_version": "1.0", "request_id": "request-1", "operation": "invoke",
        "permission": "example-intake.edit", "facts": {"project_id": "P", "owner_id": "alice"},
        "source": {"module_id": "source.library", "object_ref": {
            "object_type": "source.library.snapshot", "object_id": "fixed_1"}, "revision": 3},
        **changes,
    }


def read(case, payload=None, token=None):
    return case.client.post(
        "/api/v1/access/source-reads", json=payload or body(),
        headers={"X-Platform-Access": token or ticket(case)},
    )


def test_real_actor_reads_fixed_source_with_fresh_source_ticket(source_case):
    case = source_case
    token = ticket(case)
    response = read(case, token=token)
    assert response.status_code == 200, response.text
    assert response.json()["projection"]["data"] == {"description": "Synthetic published specimen"}
    assert response.json()["source"]["revision"] == 3
    assert case.calls[0]["ticket"] != token
    assert case.calls[0]["chain"] == ["example-intake"]
    assert token not in response.text and case.calls[0]["ticket"] not in response.text


def test_target_denied_before_source_is_contacted(source_case):
    response = read(source_case, body(facts={"project_id": "Q", "owner_id": "alice"}))
    assert response.status_code == 403
    assert source_case.calls == []


def test_source_scope_comes_from_its_store_and_denies_forged_client_scope(source_case):
    case = source_case
    with sqlite3.connect(case.storage) as db:
        db.execute("update snapshot set project = 'Q'")
    response = read(case)
    assert response.status_code == 403
    assert "projection" not in response.json()


def test_source_revocation_and_inflight_target_revocation_never_publish_data(source_case):
    case = source_case
    case.after_source = lambda: case.runtime.access.revoke(case.grant["id"], actor_id="manager")
    response = read(case)
    assert response.status_code == 403
    assert "Synthetic published" not in response.text


def test_revoked_source_and_old_ticket_cannot_reuse_success(source_case):
    case = source_case
    token = ticket(case)
    assert read(case, token=token).status_code == 200
    case.runtime.access.revoke(case.source_grant["id"], actor_id="manager")
    assert read(case, token=token).status_code == 403
    assert read(case).status_code == 403


def test_changed_source_revision_is_not_silently_accepted(source_case):
    case = source_case
    with sqlite3.connect(case.storage) as db:
        db.execute("update snapshot set revision = 4")
    response = read(case)
    assert response.status_code == 409 and "source_revision_changed" in response.text


@pytest.mark.parametrize("change", [
    {"actor": {"id": "other"}}, {"url": "http://untrusted.invalid"},
    {"source_read_version": "9.0"}, {"read_input": {"object_ref": {}}},
    {"read_input": {"operation": "write"}}, {"read_input": {"ticket": "secret"}},
])
def test_no_identity_url_operation_or_reference_override(source_case, change):
    assert read(source_case, body(**change)).status_code == 422
    assert source_case.calls == []


def test_unknown_object_and_command_cannot_be_used_as_reader(source_case):
    case = source_case
    request = body()
    request["source"]["object_ref"]["object_type"] = "source.library.unknown"
    assert read(case, request).status_code == 422
    case.source_spec["kind"] = "command"
    assert read(case).status_code == 422
    assert case.calls == []


def test_cycles_signed_depth_and_service_binding_change_fail_closed(source_case):
    case = source_case
    token = case.bridge.issue(case.actor, case.spec, "request-1", "invoke", source_chain=["source.library"])
    assert read(case, token=token).status_code == 409
    token = case.bridge.issue(case.actor, case.spec, "request-1", "invoke", source_chain=["a.one", "b.two", "c.three", "d.four"])
    assert read(case, token=token).status_code == 409
    token = ticket(case)
    case.spec["binding_revision"] += 1
    assert read(case, token=token).status_code == 403
    assert case.calls == []


def test_cookie_and_oversized_body_cannot_replace_consumer_ticket(source_case):
    case = source_case
    assert case.client.post("/api/v1/access/source-reads", json=body()).status_code == 403
    assert case.client.post("/api/v1/access/source-reads", content=b" " * 16385).status_code == 413
    assert case.calls == []
