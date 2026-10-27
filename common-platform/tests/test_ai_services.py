"""Real scoped tickets plus deterministic provider/control transports."""

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.access_bridge import AccessBridge
from app.access_control import AccessControl
from app.ai_routes import create_ai_router
from app.ai_services import AIServices
from app.db import User, create_database
from app.errors import PlatformError
from app.identity import load_actor
from app.model_gateway import ModelGateway
from app.registration_contract import validate_ai_services
from app.registry import Registry
from app.remote_services import RemoteServiceError, RemoteServiceManager
from test_access_control import definition


MODULE = "example-intake"
SERVICE = "example-intake.assistant"
DECLARATION = {
    "id": SERVICE, "title": "合成 AI 服务", "description": "只处理当前授权对象",
    "capability_ids": [MODULE + ".command"], "supported_sources": ["platform", "module"],
}


class RemoteControls:
    def __init__(self):
        self.registration = {"module_id": MODULE, "ai_services": [copy.deepcopy(DECLARATION)]}
        self.revision = 1
        self.enabled = True
        self.calls = []
        self.hook = None
        self.config = {"revision": 2, "profile": {
            "id": SERVICE, "name": "独立模块模型", "base_url": "https://module.example/v1",
            "model": "module-model", "enabled": True, "temperature": 0.2,
            "max_tokens": 512, "timeout_seconds": 10, "api_key_configured": True,
        }}

    def list_services(self):
        return {"items": [{"id": "remote-example", "revision": self.revision,
                           "registration": copy.deepcopy(self.registration), "enabled": self.enabled}]}

    def require_admitted(self, item):
        return None

    def _snapshot(self, service_id):
        assert service_id == "remote-example"
        return self.list_services()["items"][0], "synthetic-service-token", []

    def _same_revision(self, item, revision):
        if item["revision"] != revision:
            raise RemoteServiceError("remote_revision_changed", "连接已变化。")

    async def _request(self, item, token, method, endpoint, *, body=None, limit=None):
        assert token == "synthetic-service-token"
        assert endpoint in {f"ai/services/{SERVICE}/config", f"ai/services/{SERVICE}/test"}
        assert limit == 32768
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        if self.hook:
            self.hook()
        if endpoint.endswith("test"):
            assert body["expected_revision"] == self.config["revision"]
            return {"ok": True, "message": "合成测试响应", "latency_ms": 1,
                    "model": "module-model", "response_preview": "MODULE_OK"}
        if method == "PUT":
            if body["expected_revision"] != self.config["revision"]:
                raise RemoteServiceError("remote_revision_changed", "配置已变化。")
            self.config = {"revision": self.config["revision"] + 1, "profile": {
                **{key: value for key, value in body["profile"].items()
                   if key not in {"api_key", "clear_api_key"}},
                "id": SERVICE, "api_key_configured": bool(body["profile"].get("api_key")),
            }}
        return copy.deepcopy(self.config)


@pytest.fixture
def case(tmp_path):
    engine, sessions = create_database(tmp_path)
    with sessions.begin() as db:
        db.add_all([
            User(id="alice", username="alice", name="办理者", role="member", password_hash="unused", enabled=True),
            User(id="manager", username="manager", name="管理员", role="admin", password_hash="unused", enabled=True),
        ])
    with sessions() as db:
        actor, admin = load_actor(db, "alice"), load_actor(db, "manager")
    access = AccessControl(sessions)
    access.register_definition(definition(), actor_id="manager")
    grant = access.grant("alice", MODULE, MODULE + ".submitter",
                         {"project_id": "P", "owner_id": "alice"}, actor_id="manager")
    spec = {"id": MODULE + ".command", "version": "1.0", "module_id": MODULE,
            "service_id": "remote-example", "binding_revision": 1, "module_revision": 1}
    registry = Registry(tmp_path)
    registry.capabilities[spec["id"]] = spec
    registry.access_allowed = lambda current, item: any(
        access.has_permission(current, permission)
        for permission in access.capability_permissions(item["id"])
    )
    provider_calls = []
    hook = [None]

    async def provider(request):
        provider_calls.append(request)
        if hook[0]:
            await hook[0]()
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "PROVIDER_OK synthetic-provider-secret", "role": "assistant"},
            "finish_reason": "stop"}], "model": "selected-model"})

    gateway = ModelGateway(tmp_path / "models", transport=httpx.MockTransport(provider))
    profile = gateway.save_profile({"id": "selected", "name": "选定模型",
                                    "base_url": "https://selected.example/v1", "model": "selected-model",
                                    "api_key": "synthetic-provider-secret"})
    gateway.save_profile({"id": "other", "name": "其他默认模型", "base_url": "https://other.example/v1",
                          "model": "other-model", "is_default": True})
    remote = RemoteControls()
    runtime = SimpleNamespace(data_dir=tmp_path, sessions=sessions, access=access, registry=registry,
                              availability=lambda item: {"available": True}, gateway=gateway,
                              remote_services=remote)
    runtime.access_bridge = AccessBridge(runtime)
    service = AIServices(runtime)
    app = FastAPI()
    app.include_router(create_ai_router(service, lambda: admin, lambda: admin))

    @app.exception_handler(PlatformError)
    async def error_handler(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    with TestClient(app) as client:
        yield SimpleNamespace(service=service, runtime=runtime, actor=actor, admin=admin,
                              access=access, grant=grant, spec=spec, gateway=gateway, remote=remote,
                              provider_calls=provider_calls, hook=hook, sessions=sessions,
                              client=client, profile=profile)
    engine.dispose()


def scope(**extra):
    return {"service_id": SERVICE, "request_id": "request-1", "operation": "invoke",
            "permission": MODULE + ".edit", "facts": {"project_id": "P", "owner_id": "alice"}, **extra}


def ticket(case):
    return case.runtime.access_bridge.issue(case.actor, case.spec, "request-1", "invoke")


async def bind(case, source="platform", revision=0, profile="selected"):
    return await case.service.save_binding(case.admin, MODULE, SERVICE, {
        "expected_revision": revision, "source": source,
        "platform_profile_id": profile if source == "platform" else None,
    })


async def chat(case, token=None, revision=1):
    return await case.service.chat(token or ticket(case), scope(
        binding_revision=revision, module_config_revision=None,
        platform_config_digest=case.service._profile_stamp("selected"),
        messages=[{"role": "user", "content": "synthetic question"}]))


def test_optional_declaration_is_backward_compatible_and_never_grants():
    registration = {"module_id": MODULE, "permissions": {"mode": "platform_access"}}
    manifest = {"capabilities": [{"id": MODULE + ".command"}]}
    validate_ai_services(registration, manifest)
    validate_ai_services({**registration, "ai_services": [DECLARATION]}, manifest)
    for changes in ({"id": "other.assistant"}, {"capability_ids": [MODULE + ".missing"]}):
        with pytest.raises(ValueError):
            validate_ai_services({**registration, "ai_services": [{**DECLARATION, **changes}]}, manifest)
    with pytest.raises(ValueError):
        validate_ai_services({**registration, "permissions": {"mode": "capability_roles"},
                              "ai_services": [DECLARATION]}, manifest)
    with pytest.raises(ValueError):
        validate_ai_services({**registration, "ai_services": [DECLARATION, DECLARATION]}, manifest)


async def test_default_disabled_never_implicitly_uses_a_platform_default(case):
    assert case.service.list_services(case.admin)["items"][0]["source"] == "disabled"
    with pytest.raises(PlatformError, match="尚未启用"):
        await case.service.resolve(ticket(case), scope())
    assert not case.provider_calls


async def test_explicit_platform_profile_no_key_or_default_fallback(case):
    await bind(case)
    result = await chat(case)
    assert case.provider_calls[0].url.host == "selected.example"
    assert case.provider_calls[0].headers["authorization"] == "Bearer synthetic-provider-secret"
    assert result["source"] == "platform" and result["binding_revision"] == 1
    assert "synthetic-provider-secret" not in json.dumps(result)
    assert "api_key" not in case.service.path.read_text(encoding="utf-8")
    assert not case.remote.calls


async def test_source_switch_is_explicit_revision_checked_and_persistent(case):
    first = await bind(case)
    with pytest.raises(PlatformError) as conflict:
        await bind(case, "module", revision=0)
    assert conflict.value.status == 409
    assert case.service.list_services(case.admin)["items"][0]["revision"] == first["revision"]
    await bind(case, "module", revision=1)
    restarted = AIServices(case.runtime)
    route = await restarted.resolve(ticket(case), scope())
    assert route == {"service_id": SERVICE, "source": "module", "binding_revision": 2,
                     "module_config_revision": 2, "platform_config_digest": None}
    with pytest.raises(PlatformError) as mismatch:
        await chat(case, revision=2)
    assert mismatch.value.code == "ai_source_mismatch" and not case.provider_calls


async def test_module_config_change_during_business_call_rejects_old_output(case):
    await bind(case, "module")
    token = ticket(case)
    route = await case.service.resolve(token, scope())
    case.remote.config["revision"] += 1
    with pytest.raises(PlatformError) as changed:
        await case.service.validate(token, scope(binding_revision=route["binding_revision"],
                                                module_config_revision=route["module_config_revision"],
                                                platform_config_digest=None))
    assert changed.value.code == "ai_module_config_changed"


@pytest.mark.parametrize("change", ["binding", "profile", "grant", "user", "connection"])
async def test_current_authority_and_config_rechecked_after_provider_response(case, change):
    await bind(case)

    async def mutate():
        if change == "binding":
            await bind(case, "disabled", revision=1)
        elif change == "profile":
            case.gateway.save_profile({"id": "selected", "model": "changed-model"})
        elif change == "grant":
            case.access.revoke(case.grant["id"], actor_id="manager")
        elif change == "user":
            with case.sessions.begin() as db:
                db.get(User, "alice").enabled = False
        else:
            case.spec["binding_revision"] += 1

    case.hook[0] = mutate
    with pytest.raises(PlatformError) as rejected:
        await chat(case)
    assert rejected.value.status in {403, 409}
    assert len(case.provider_calls) == 1


@pytest.mark.parametrize("change", ["disabled", "deleted"])
async def test_disabled_or_deleted_profile_rejected_without_fallback(case, change):
    await bind(case)
    if change == "disabled":
        case.gateway.save_profile({"id": "selected", "enabled": False})
    else:
        case.gateway.delete_profile("selected")
    with pytest.raises(PlatformError):
        await chat(case)
    assert not case.provider_calls


@pytest.mark.parametrize("extra", [
    {"request_id": "different"}, {"operation": "objects.read"},
    {"permission": MODULE + ".review"}, {"facts": {"project_id": "other", "owner_id": "alice"}},
    {"service_id": "other.assistant"},
])
async def test_ticket_cannot_change_scope_operation_or_declared_ai_service(case, extra):
    await bind(case)
    with pytest.raises(PlatformError):
        await case.service.resolve(ticket(case), scope(**extra))
    assert not case.provider_calls


async def test_unrelated_capability_of_same_module_cannot_use_service(case):
    await bind(case)
    case.remote.registration["ai_services"][0]["capability_ids"] = [MODULE + ".read"]
    await bind(case, revision=1)
    with pytest.raises(PlatformError) as denied:
        await case.service.resolve(ticket(case), scope())
    assert denied.value.code == "ai_capability_mismatch"


async def test_connection_change_stales_binding_and_restart_invalidates_ticket(case):
    await bind(case)
    token = ticket(case)
    case.runtime.access_bridge = AccessBridge(case.runtime)
    with pytest.raises(PlatformError):
        await case.service.resolve(token, scope())
    case.remote.revision += 1
    case.spec["binding_revision"] += 1
    assert case.service.list_services(case.admin)["items"][0]["binding_stale"]
    with pytest.raises(PlatformError) as stale:
        await case.service.resolve(ticket(case), scope())
    assert stale.value.code == "ai_binding_stale"


async def test_module_disabled_and_unavailable_do_not_contact_a_provider(case):
    await bind(case)
    case.remote.enabled = False
    with pytest.raises(PlatformError):
        await case.service.resolve(ticket(case), scope())
    case.remote.enabled = True
    case.runtime.availability = lambda spec: {"available": False}
    with pytest.raises(PlatformError):
        await case.service.resolve(ticket(case), scope())
    assert not case.provider_calls


async def test_configuration_management_requires_current_admin(case):
    with pytest.raises(PlatformError) as denied:
        await case.service.module_config(case.actor, MODULE, SERVICE)
    assert denied.value.status == 403 and not case.remote.calls

    def revoke_admin():
        with case.sessions.begin() as db:
            db.get(User, "manager").enabled = False

    case.remote.hook = revoke_admin
    with pytest.raises(PlatformError) as rejected:
        await case.service.module_config(case.admin, MODULE, SERVICE)
    assert rejected.value.code == "actor_disabled"


async def test_module_config_control_is_fixed_path_bounded_and_secret_input_only(case):
    payload = {"expected_revision": 2, "profile": {
        key: value for key, value in case.remote.config["profile"].items()
        if key not in {"id", "api_key_configured"}
    }}
    payload["profile"]["api_key"] = "synthetic-module-secret"
    response = await case.service.module_config(case.admin, MODULE, SERVICE, payload)
    assert response["revision"] == 3 and response["profile"]["api_key_configured"]
    assert "synthetic-module-secret" not in json.dumps(response)
    assert not case.service.path.exists()
    assert not case.provider_calls


async def test_module_config_response_cannot_echo_secrets_or_unknown_fields(case):
    case.remote.config["profile"]["api_key"] = "do-not-echo"
    with pytest.raises(PlatformError):
        await case.service.module_config(case.admin, MODULE, SERVICE)


async def test_control_connection_revision_rechecked_after_network_io(case):
    case.remote.hook = lambda: setattr(case.remote, "revision", 2)
    with pytest.raises(PlatformError) as changed:
        await case.service.module_config(case.admin, MODULE, SERVICE)
    assert changed.value.code == "ai_connection_changed"


async def test_explicit_test_uses_only_saved_source(case):
    await bind(case)
    result = await case.service.test(case.admin, MODULE, SERVICE, {"expected_revision": 1})
    assert result["source"] == "platform" and len(case.provider_calls) == 1
    await bind(case, "module", revision=1)
    result = await case.service.test(case.admin, MODULE, SERVICE, {"expected_revision": 2})
    assert result["source"] == "module" and len(case.provider_calls) == 1
    assert case.remote.calls[-2][0] == "POST"


async def test_provider_timeout_returns_safe_error_without_retry_or_fallback(case):
    await bind(case)

    async def timeout():
        raise httpx.ReadTimeout("provider raw secret must not escape")

    case.hook[0] = timeout
    with pytest.raises(PlatformError) as failed:
        await chat(case)
    assert failed.value.code == "model_timeout" and failed.value.status == 504
    assert "raw secret" not in failed.value.message
    assert len(case.provider_calls) == 1 and not case.remote.calls


def test_callbacks_bound_stream_size_and_do_not_accept_cookie_as_authority(case):
    response = case.client.post("/api/v1/ai/resolve", json=scope(),
                                headers={"Cookie": "common_framework_session=synthetic"})
    assert response.status_code == 403
    for path, limit in (("resolve", 16384), ("validate", 16384), ("chat", 65536)):
        response = case.client.post("/api/v1/ai/" + path, content=b" " * (limit + 1))
        assert response.status_code == 413
    response = case.client.put(f"/api/v1/ai-services/{MODULE}/{SERVICE}/module-config",
                               content=b" " * 32769)
    assert response.status_code == 413


def test_validation_errors_do_not_echo_input_secrets(case):
    secret = "synthetic-sensitive-invalid-input"
    response = case.client.put(f"/api/v1/ai-services/{MODULE}/{SERVICE}/binding",
                               json={"api_key": secret})
    assert response.status_code == 422 and secret not in response.text


async def test_corrupt_binding_state_fails_closed(case):
    await bind(case)
    case.service.path.write_text('{"version":1,"items":[{"api_key":"synthetic"}]}', encoding="utf-8")
    with pytest.raises(PlatformError) as corrupt:
        await case.service.resolve(ticket(case), scope())
    assert corrupt.value.code == "ai_config_corrupt" and not case.provider_calls


async def test_concurrent_source_saves_have_one_winner(case):
    await bind(case)
    outcomes = await asyncio.gather(bind(case, "module", revision=1),
                                    bind(case, "disabled", revision=1), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, PlatformError) and item.code == "ai_revision_conflict"
               for item in outcomes) == 1
    assert case.service.list_services(case.admin)["items"][0]["revision"] == 2


async def test_ticket_expiry_during_model_response_rejects_result(case):
    await bind(case)
    clock = [1_800_000_000.0]
    case.runtime.access_bridge.clock = lambda: clock[0]
    token = ticket(case)

    async def expire():
        clock[0] += 46

    case.hook[0] = expire
    with pytest.raises(PlatformError) as expired:
        await chat(case, token)
    assert expired.value.code == "access_ticket_expired"
    assert len(case.provider_calls) == 1


async def test_platform_request_rejects_foreign_module_revision_before_provider(case):
    await bind(case)
    with pytest.raises(PlatformError) as invalid:
        await case.service.chat(ticket(case), scope(binding_revision=1, module_config_revision=2,
                                                    platform_config_digest=case.service._profile_stamp("selected"),
                                                    messages=[{"role": "user", "content": "hello"}]))
    assert invalid.value.status == 422 and not case.provider_calls


@pytest.mark.parametrize("window", ["before_chat", "after_chat"])
async def test_platform_digest_rejects_old_receipt_across_both_commit_windows(case, window):
    await bind(case)
    token = ticket(case)
    route = await case.service.resolve(token, scope())
    call = scope(binding_revision=route["binding_revision"], module_config_revision=None,
                 platform_config_digest=route["platform_config_digest"])
    if window == "after_chat":
        await case.service.chat(token, {**call, "messages": [{"role": "user", "content": "hello"}]})
    case.gateway.save_profile({"id": "selected", "api_key": "synthetic-replaced-secret"})
    with pytest.raises(PlatformError) as changed:
        if window == "before_chat":
            await case.service.chat(token, {**call, "messages": [{"role": "user", "content": "hello"}]})
        else:
            await case.service.validate(token, call)
    assert changed.value.code == "ai_platform_config_changed"
    assert len(case.provider_calls) == (1 if window == "after_chat" else 0)


async def test_remote_control_transport_preserves_safe_status_and_fixed_auth(tmp_path):
    seen = []
    status = [409]

    def remote(request):
        seen.append(request)
        return httpx.Response(status[0], json={"error": {"message": "private provider detail synthetic-key"}})

    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(remote))
    connection = {"base_url": "https://module.example", "timeout_seconds": 2}
    for number in (409, 413, 422, 503, 504):
        status[0] = number
        with pytest.raises(PlatformError) as error:
            await manager._request(connection, "synthetic-service-token", "PUT",
                                   f"ai/services/{SERVICE}/config", body={"synthetic": True}, limit=32768)
        assert error.value.status == number
        assert "synthetic-key" not in error.value.message
        assert "provider detail" not in error.value.message
    assert all(str(item.url) == f"https://module.example/platform/v1/ai/services/{SERVICE}/config"
               and item.headers["authorization"] == "Bearer synthetic-service-token" for item in seen)


async def test_remote_control_body_limit_and_timeout_have_no_automatic_retry(tmp_path):
    calls = []

    def oversized(request):
        calls.append(request)
        return httpx.Response(200, content=b" " * 32769)

    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(oversized))
    item = {"base_url": "https://module.example", "timeout_seconds": 1}
    with pytest.raises(RemoteServiceError) as too_large:
        await manager._request(item, "synthetic-service-token", "GET", f"ai/services/{SERVICE}/config", limit=32768)
    assert too_large.value.code == "remote_response_too_large" and len(calls) == 1

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic-key")

    manager._transport = httpx.MockTransport(timeout)
    with pytest.raises(RemoteServiceError) as timed_out:
        await manager._request(item, "synthetic-service-token", "GET", f"ai/services/{SERVICE}/config", limit=32768)
    assert timed_out.value.code == "remote_timeout" and len(calls) == 2
    assert "synthetic-key" not in timed_out.value.message


def test_real_app_ai_admin_and_csrf_boundaries(tmp_path):
    from app.main import create_app

    application = create_app(tmp_path, start_worker=False)
    with TestClient(application) as client:
        assert client.get("/api/v1/ai-services").status_code == 401
        member = client.post("/api/v1/auth/login", json={"username": "member", "password": "demo-member-2026"})
        assert member.status_code == 200
        assert client.get("/api/v1/ai-services").status_code == 403
        admin = client.post("/api/v1/auth/login", json={"username": "admin", "password": "demo-admin-2026"})
        assert admin.status_code == 200
        assert client.get("/api/v1/ai-services").json() == {"items": []}
        endpoint = f"/api/v1/ai-services/{MODULE}/{SERVICE}/binding"
        assert client.put(endpoint, json={}).status_code == 403
        invalid = client.put(endpoint, json={"api_key": "synthetic-secret-not-in-errors"},
                             headers={"X-CSRF-Token": admin.json()["csrf_token"]})
        assert invalid.status_code == 422 and "synthetic-secret-not-in-errors" not in invalid.text
