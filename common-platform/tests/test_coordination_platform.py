"""Real platform identity checks with the independent center's ASGI HTTP boundary."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.coordination_client import CoordinationClient
from app.coordination_routes import create_coordination_router
from app.db import User, create_database
from app.errors import PlatformError, require
from app.identity import load_actor
from app.remote_routes import create_remote_router
from coordination_service.main import create_app
from coordination_service.settings import provision
from monitoring_service.settings import unseal


PREFIX = "/api/v1/coordination"
SCOPE = {"organization_id": "org-1", "project_id": "project-1"}


class Modules:
    def __init__(self):
        self.items = []
        for name in ("source", "mining"):
            declaration = {"version": "1.0", "machines": [{"id": name + ".machine", "object_types": [name + ".item"],
                           "states": ["pending", "public"], "public_states": ["public"], "approval_required": True}],
                           "emits": ["publication.requested", "resource.published"],
                           "accepts": [] if name == "source" else [{"source_module": "source", "kind": "resource.published", "mode": "notify"}]}
            self.items.append({"id": "service-" + name, "revision": 1, "discovered_revision": 1, "enabled": True,
                               "admission": {"passed": True, "revision": 1},
                               "manifest": {"module": {"id": name, "name": name}}, "registration": {"coordination": declaration}})

    def list_services(self):
        return {"items": deepcopy(self.items)}


def command(revision=0):
    return {"expected_revision": revision, "module_ids": ["source", "mining"],
            "subscriptions": [{"id": "publication-mining", "source_module": "source", "target_module": "mining",
                               "kind": "resource.published", "scope": deepcopy(SCOPE), "mode": "notify"}]}


@pytest.fixture
def case(tmp_path):
    directory, center_dir = tmp_path / "platform", tmp_path / "center"
    provision(directory, center_dir, 8041)
    center = create_app(center_dir, start_worker=False)
    manager = Modules()
    bridge = CoordinationClient(directory, manager, transport=httpx.ASGITransport(app=center))
    engine, sessions = create_database(directory)
    with sessions.begin() as db:
        db.add_all([User(id="manager", username="manager", name="管理员", role="admin", password_hash="unused", enabled=True),
                    User(id="member", username="member", name="办理人", role="member", password_hash="unused", enabled=True)])
    with sessions() as db:
        actors = {name: load_actor(db, name) for name in ("manager", "member")}

    def actor(request: Request):
        return actors[request.headers.get("X-Actor", "manager")]

    def csrf(request: Request):
        require(request.headers.get("X-CSRF-Token") == "synthetic-csrf", "csrf_failed", "需要当前会话校验。", 403)

    runtime = SimpleNamespace(sessions=sessions, lock=asyncio.Lock())
    def publish(service_id, enabled, revision):
        item = next(i for i in manager.items if i["id"] == service_id)
        item["enabled"] = enabled
        item["revision"] += 1
        return {"id": service_id, "enabled": enabled}
    runtime.set_remote_publication = publish
    app = FastAPI()
    app.state.coordination = bridge
    app.include_router(create_coordination_router(bridge, runtime, actor, csrf))
    app.include_router(create_remote_router(manager, runtime, actor, csrf), prefix="/api/v1")

    @app.exception_handler(PlatformError)
    async def error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    with TestClient(app) as client, TestClient(center) as center_client:
        yield SimpleNamespace(client=client, center=center_client, store=center.state.coordination, bridge=bridge,
                              manager=manager, directory=directory, sessions=sessions, actors=actors)
    engine.dispose()


def configure(case, value=None):
    return case.client.put(PREFIX + "/configuration", json=value or command(), headers={"X-CSRF-Token": "synthetic-csrf"})


def token(case, module):
    value = json.loads((case.directory / "coordination-module-secrets.json").read_text(encoding="utf-8"))
    return unseal(case.directory, value[module])


def pending(case, event_id="request-1"):
    value = {"version": "1.0", "event_id": event_id, "module_id": "source", "kind": "publication.requested",
             "machine_id": "source.machine", "object_ref": {"object_type": "source.item", "object_id": "o-1"},
             "state": "pending", "revision": 1, "scope": SCOPE}
    assert case.center.post(PREFIX + "/events", json=value, headers={"Authorization": "Bearer " + token(case, "source")}).status_code == 200


def test_configuration_uses_only_admitted_declarations_and_generated_tokens(case):
    assert configure(case).status_code == 200
    public = case.client.get(PREFIX + "/configuration").json()
    assert public["status"] == "confirmed" and len(public["modules"]) == 2
    serialized = json.dumps(public)
    assert token(case, "source") not in serialized and '"token"' not in serialized
    bad = {**command(1), "modules": [{"token": "browser-supplied-token"}]}
    assert configure(case, bad).status_code == 422
    bad = command(1)
    bad["module_ids"].append("unregistered")
    assert configure(case, bad).status_code == 422
    assert case.client.get(PREFIX + "/summary").json()["summary"]["config_revision"] == 1


@pytest.mark.parametrize("surface", ["summary", "configuration"])
def test_member_cannot_manage_or_read_control_surface(case, surface):
    assert case.client.get(PREFIX + "/" + surface, headers={"X-Actor": "member"}).status_code == 403


def test_mutations_require_csrf_and_cannot_spoof_actor(case):
    assert case.client.put(PREFIX + "/configuration", json=command()).status_code == 403
    assert configure(case).status_code == 200
    pending(case)
    decision = {"expected_revision": 1, "decision": "approved"}
    assert case.client.post(PREFIX + "/approvals/request-1/decide", json=decision).status_code == 403
    response = case.client.post(PREFIX + "/approvals/request-1/decide", json={**decision, "actor_id": "someone-else"}, headers={"X-CSRF-Token": "synthetic-csrf"})
    assert response.status_code == 422
    response = case.client.post(PREFIX + "/approvals/request-1/decide", json=decision, headers={"X-CSRF-Token": "synthetic-csrf"})
    assert response.status_code == 200 and response.json()["approval"]["actor_id"] == "manager"


@pytest.mark.parametrize("mutation", ["disable", "revision", "admission", "declaration"])
def test_module_binding_revocation_reaches_center(case, mutation):
    configure(case)
    source_token = token(case, "source")
    if mutation == "disable":
        case.manager.items[0]["enabled"] = False
    if mutation == "revision":
        case.manager.items[0]["revision"] = 2
    if mutation == "admission":
        case.manager.items[0]["admission"]["passed"] = False
    if mutation == "declaration":
        case.manager.items[0]["registration"]["coordination"]["machines"][0]["states"].append("withdrawn")
    asyncio.run(case.bridge.reconcile())
    with pytest.raises(Exception) as denied:
        case.store.authorize_module(source_token)
    assert getattr(denied.value, "status", None) == 403
    saved = case.bridge._settings()
    assert saved["bindings"]["source"]["enabled"] is False
    case.manager.items = Modules().items
    asyncio.run(case.bridge.reconcile())
    with pytest.raises(Exception):
        case.store.authorize_module(source_token)


def test_summary_rejects_remote_extras_and_never_returns_old_data(case):
    configure(case)
    pending(case)
    value = case.client.get(PREFIX + "/summary").json()
    assert value["state"] == "connected" and len(value["summary"]["approvals"]) == 1

    async def untrusted(request):
        return httpx.Response(200, json={**value["summary"], "secret": "synthetic-remote-secret"})

    case.bridge.transport = httpx.MockTransport(untrusted)
    response = case.client.get(PREFIX + "/summary")
    assert response.json() == {"state": "unavailable", "summary": None}
    assert "synthetic-remote-secret" not in response.text


def test_malformed_write_response_reports_unknown_and_preserves_intent(case):
    async def untrusted(request):
        return httpx.Response(200, json={"version": "1.0", "config_revision": 1, "payload": "synthetic-remote-secret"})

    case.bridge.transport = httpx.MockTransport(untrusted)
    response = configure(case)
    assert response.status_code == 503 and response.json()["error"]["code"] == "coordination_outcome_unknown"
    assert "synthetic-remote-secret" not in response.text
    assert case.bridge.settings()["status"] == "pending"
    assert not (case.directory / "coordination-settings.json").exists()


def test_known_config_conflict_preserves_confirmed_settings(case):
    configure(case)
    original = case.bridge._settings()
    conflict = command(0)
    conflict["subscriptions"] = []
    assert configure(case, conflict).status_code == 409
    assert case.bridge._settings() == original
    assert case.bridge.settings()["status"] == "confirmed"


def test_authorization_change_during_read_hides_result(case, monkeypatch):
    configure(case)
    original = case.bridge.request

    async def revoke_after_response(*args, **kwargs):
        value = await original(*args, **kwargs)
        with case.sessions.begin() as db:
            db.get(User, "manager").role = "member"
        return value

    monkeypatch.setattr(case.bridge, "request", revoke_after_response)
    response = case.client.get(PREFIX + "/summary")
    assert response.status_code == 403
    assert "summary" not in response.json()


def test_admin_revoked_before_decision_dispatch_never_sends_it(case, monkeypatch):
    configure(case)
    pending(case)

    async def revoke():
        with case.sessions.begin() as db:
            db.get(User, "manager").enabled = False

    monkeypatch.setattr(case.bridge, "reconcile", revoke)
    response = case.client.post(PREFIX + "/approvals/request-1/decide", json={"expected_revision": 1, "decision": "approved"}, headers={"X-CSRF-Token": "synthetic-csrf"})
    assert response.status_code == 403
    assert case.store.summary(case.bridge.connection()[1])["approvals"][0]["status"] == "pending"


def test_bad_decision_json_and_oversize_are_bounded(case):
    configure(case)
    headers = {"X-CSRF-Token": "synthetic-csrf"}
    assert case.client.post(PREFIX + "/approvals/request-1/decide", content="{bad", headers=headers).status_code == 422
    assert case.client.post(PREFIX + "/approvals/request-1/decide", content=" " * 1025, headers=headers).status_code == 413


def test_lost_configuration_response_is_recovered_from_actual_center(case):
    transport = case.bridge.transport

    async def lost_response(request):
        await transport.handle_async_request(request)
        raise httpx.ReadError("synthetic acknowledgement loss", request=request)

    case.bridge.transport = httpx.MockTransport(lost_response)
    assert configure(case).json()["error"]["code"] == "coordination_outcome_unknown"
    original_token = token(case, "source")
    assert case.bridge.settings()["status"] == "pending"
    case.bridge.transport = transport
    assert case.client.get(PREFIX + "/summary").json()["state"] == "connected"
    assert case.bridge.settings()["status"] == "confirmed"
    assert token(case, "source") == original_token
    case.store.authorize_module(original_token)


def test_unknown_first_configuration_still_observes_module_revocation(case):
    transport = case.bridge.transport

    async def lost_response(request):
        await transport.handle_async_request(request)
        return httpx.Response(200, json={"private": "unexpected"})

    case.bridge.transport = httpx.MockTransport(lost_response)
    assert configure(case).status_code == 503
    original_token = token(case, "source")
    case.manager.items[0]["enabled"] = False
    case.bridge.transport = transport
    assert case.client.get(PREFIX + "/summary").json()["state"] == "connected"
    with pytest.raises(Exception) as denied:
        case.store.authorize_module(original_token)
    assert getattr(denied.value, "status", None) == 403


def test_unapplied_uncertain_configuration_is_never_replayed(case):
    transport = case.bridge.transport

    async def not_received(request):
        raise httpx.ConnectError("synthetic disconnected center", request=request)

    case.bridge.transport = httpx.MockTransport(not_received)
    assert configure(case).status_code == 503
    case.bridge.transport = transport
    response = case.client.get(PREFIX + "/summary").json()
    assert response["state"] == "connected" and response["summary"]["config_revision"] == 0
    assert case.store.configuration(case.bridge.connection()[1])["modules"] == []
    assert case.bridge.settings()["status"] == "pending"


def test_remote_disable_route_revokes_coordination_token_before_success(case):
    configure(case)
    original_token = token(case, "source")
    response = case.client.post("/api/v1/remote-services/service-source/disable", json={"expected_revision": 1},
                                headers={"X-CSRF-Token": "synthetic-csrf"})
    assert response.status_code == 200
    with pytest.raises(Exception) as denied:
        case.store.authorize_module(original_token)
    assert getattr(denied.value, "status", None) == 403
