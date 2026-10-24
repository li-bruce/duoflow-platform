"""Exercise the portable access client against the real platform authorization bridge."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx

SOURCE = Path(__file__).resolve().parents[1] / "templates/module-kit/sdk/python/remote_access.py"
spec = importlib.util.spec_from_file_location("platform_remote_access_client", SOURCE)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
RemoteAccessClient = sdk.RemoteAccessClient
ENDPOINT = "http://127.0.0.1/api/v1/access/remote-decisions"
REQUEST = dict(
    ticket="synthetic-ticket",
    request_id="req:one",
    operation="objects.read",
    permission="sample.item.read",
    facts={"project_id": "P", "owner_id": "alice"},
)


def run(client, **changes):
    return asyncio.run(client.decide(**{**REQUEST, **changes}))


def test_real_access_bridge_scope_operation_and_revocation(tmp_path):
    # Import platform components only. app.main would create a default runtime.
    from fastapi import FastAPI
    from app.access_bridge import AccessBridge, create_access_bridge_router
    from app.access_control import AccessControl
    from app.db import User, create_database
    from app.identity import load_actor
    from app.registry import Registry

    engine, sessions = create_database(tmp_path / "isolated-db")
    try:
        with sessions.begin() as db:
            db.add(
                User(
                    id="alice",
                    username="synthetic-alice",
                    name="合成用户",
                    role="member",
                    password_hash="unused",
                    enabled=True,
                )
            )
        with sessions() as db:
            actor = load_actor(db, "alice")
        access = AccessControl(sessions)
        access.register_definition(
            {
                "contract_version": "1.0",
                "module_id": "sample",
                "version": "1.0.0",
                "title": "合成权限",
                "scope_keys": ["project_id", "owner_id"],
                "permissions": [{"id": "sample.item.read", "title": "读取"}],
                "role_templates": [
                    {
                        "id": "sample.reader",
                        "title": "范围读取者",
                        "permissions": ["sample.item.read"],
                        "required_scope": ["project_id", "owner_id"],
                    }
                ],
                "command_mappings": [
                    {"capability_id": "sample.item.read", "permission": "sample.item.read"}
                ],
                "experiences": [],
                "workitem_sources": [],
            },
            actor_id="synthetic-manager",
        )
        grant = access.grant(
            "alice",
            "sample",
            "sample.reader",
            {"project_id": "P", "owner_id": "alice"},
            actor_id="synthetic-manager",
        )
        cap = {
            "id": "sample.item.read",
            "version": "1.0.0",
            "module_id": "sample",
            "module_revision": 1,
            "service_id": "synthetic-service",
            "binding_revision": 1,
        }
        registry = Registry(tmp_path)
        registry.capabilities[cap["id"]] = cap
        registry.access_allowed = lambda current, item: any(
            access.has_permission(current, p) for p in access.capability_permissions(item["id"])
        )
        runtime = SimpleNamespace(
            sessions=sessions,
            access=access,
            registry=registry,
            availability=lambda _: {"available": True},
        )
        bridge = AccessBridge(runtime)
        app = FastAPI()
        app.include_router(create_access_bridge_router(bridge))
        client = RemoteAccessClient(ENDPOINT, transport=httpx.ASGITransport(app=app))
        ticket = bridge.issue(actor, cap, REQUEST["request_id"], REQUEST["operation"])
        allowed = run(client, ticket=ticket)
        assert allowed["allowed"] is True and allowed["policy_revision"] > 0
        assert (
            run(client, ticket=ticket, facts={"project_id": "Q", "owner_id": "alice"})["reason"]
            == "grant_scope_mismatch"
        )
        assert (
            run(client, ticket=ticket, operation="invoke")["reason"]
            == "access_ticket_operation_mismatch"
        )
        assert (
            run(client, ticket=ticket, request_id="req:another")["reason"]
            == "access_ticket_request_mismatch"
        )
        access.revoke(grant["id"], actor_id="synthetic-manager")
        assert run(client, ticket=ticket)["reason"] == "access_authorization_changed"
        assert ticket not in json.dumps(allowed)
    finally:
        engine.dispose()
