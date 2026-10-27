"""Fine-grained read grants must not multiply catalog RBAC evaluations."""

from copy import deepcopy

import casbin

from app.access_control import AccessControl, CasbinDecisionEngine


def grants():
    return [{"id": f"g-{number}", "role_id": "example.reader", "permissions": ["example.read"]}
            for number in range(180)] + [
                {"id": "writer", "role_id": "example.writer", "permissions": ["example.write"]}
            ]


def test_catalog_short_circuits_one_current_domain_without_caching(monkeypatch):
    current = grants()
    service = AccessControl(lambda: None)
    monkeypatch.setattr(service, "_context", lambda actor: ({}, deepcopy(current)))
    original = casbin.Enforcer.enforce
    calls = []
    def count(self, *args):
        calls.append(args)
        return original(self, *args)
    monkeypatch.setattr(casbin.Enforcer, "enforce", count)
    assert service.has_permission({"id": "alice"}, "example.read")
    assert len(calls) == 1
    current.clear()
    assert not service.has_permission({"id": "alice"}, "example.read")


def test_action_filter_preserves_all_allowed_grant_ids_and_independent_domains():
    engine = CasbinDecisionEngine()
    value = grants()
    assert engine.allowed_grants("alice", "example.write", value) == ["writer"]
    assert engine.allowed_grants("alice", "example.missing", value) == []
    assert engine.allowed_grants("alice", "example.read", value[:2]) == ["g-0", "g-1"]
    assert value[0]["permissions"] == ["example.read"]
