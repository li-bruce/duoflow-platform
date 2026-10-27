"""Remote service contracts and credential boundaries, with no external requests."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.remote_services import RemoteServiceError, RemoteServiceManager
from registration_helpers import complete_admission


TOKEN = "synthetic-remote-token-DO-NOT-USE-123456"


def connection(**changes):
    return {"id": "remote-test", "name": "合成远程服务", "base_url": "https://service.example/prefix",
            "timeout_seconds": 12, **changes}


def manifest():
    return {"protocol_version": "1.0", "module": {"id": "remote.echo", "version": "1.0.0", "name": "合成回显模块", "description": "测试用远程模块"},
            "capabilities": [{"id": "remote.echo.run", "version": "1.0.0", "title": "回显", "description": "返回输入文本",
                              "kind": "command", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
                              "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                              "roles": ["member", "admin"], "execution": {"mode": "background", "confirmation": True},
                              "presentation": {"page": {"renderer": "text-result"}, "conversation": {"renderer": "text-result"}},
                              "result": {"resource_type": "remote.echo", "title": "回显结果", "text_artifact": {"field": "text", "name": "echo.txt"}}}]}


def request(**changes):
    return {"protocol_version": "1.0", "request_id": "stable-invocation-id", "capability_id": "remote.echo.run",
            "capability_version": "1.0.0", "input": {"text": "合成输入"},
            "context": {"workspace_id": "local", "actor": {"id": "synthetic-member", "role": "member"}}, **changes}


def completed(sent=None, **changes):
    sent = sent or request()
    return {**{key: sent[key] for key in ("protocol_version", "request_id", "capability_id", "capability_version")},
            "summary": "已生成合成结果。", "data": {"text": "合成输出"}, **changes}


def run(coroutine):
    return asyncio.run(coroutine)


def ready(tmp_path, invoke_handler=None):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/manifest"):
            return httpx.Response(200, json=manifest())
        if req.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True})
        return invoke_handler(req) if invoke_handler else httpx.Response(200, json=completed(json.loads(req.content)))

    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(handler))
    manager.save_connection(connection(token=TOKEN))
    discovered = run(manager.discover("remote-test"))
    complete_admission(manager, 'remote-test')
    manager.set_enabled("remote-test", True, discovered["revision"])
    return manager, calls


def test_connection_save_is_offline_encrypted_restartable_and_public_metadata_has_no_token(tmp_path):
    calls = []
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(lambda req: calls.append(req)))
    saved = manager.save_connection(connection(token=TOKEN))
    assert calls == []
    assert saved["revision"] == 1 and saved["enabled"] is False and saved["manifest"] is None
    assert saved["token_configured"] and saved["discovered_revision"] is None
    assert TOKEN not in json.dumps(manager.list_services())
    assert "ciphertext" not in json.dumps(manager.list_services())
    assert all(TOKEN.encode() not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    restarted = RemoteServiceManager(tmp_path)
    assert restarted.list_services() == manager.list_services()
    assert restarted.redact("before " + TOKEN + " after") == "before 【凭据已隐藏】 after"
    assert restarted.secrets_storage in {"windows-dpapi", "fernet-local-file"}


@pytest.mark.parametrize("url", ["http://localhost:8021", "http://127.0.0.1:8021/path/", "http://[::1]:8021", "https://remote.example/service"])
def test_explicit_admin_local_or_https_destinations_are_allowed(tmp_path, url):
    saved = RemoteServiceManager(tmp_path).save_connection(connection(base_url=url))
    assert saved["base_url"] == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://remote.example", "https://user:password@example.test", "https://example.test?token=private",
                                "https://example.test#private", "https://example.test?", "https://example.test\\@localhost",
                                "file:///tmp/x", "https://example.test:999999", "https://us%65r@example.test", "https://example.test/\npath"])
def test_unsafe_destinations_rejected_without_echo(tmp_path, url):
    with pytest.raises(RemoteServiceError) as error:
        RemoteServiceManager(tmp_path).save_connection(connection(base_url=url))
    assert error.value.code == "invalid_remote_url"
    assert url not in error.value.message


def test_connection_revisions_discovery_enable_and_noop_save(tmp_path):
    manager, calls = ready(tmp_path)
    public = manager.list_services()["items"][0]
    assert public["enabled"] and public["discovered_revision"] == public["revision"] == 2
    unchanged = manager.save_connection({"id": "remote-test", "token": "", "timeout_seconds": 12})
    assert unchanged == public
    assert len(calls) == 1
    changed = manager.save_connection({"id": "remote-test", "timeout_seconds": 13})
    assert changed["revision"] == 3 and not changed["enabled"] and changed["manifest"] is None
    assert changed["discovered_revision"] is None and changed["token_configured"]
    assert manager.enabled_bindings() == []
    with pytest.raises(RemoteServiceError) as stale:
        manager.set_enabled("remote-test", True, 1)
    assert stale.value.code == "remote_revision_changed"
    with pytest.raises(RemoteServiceError) as pending:
        manager.set_enabled("remote-test", True, 3)
    assert pending.value.code == "remote_not_discovered"
    run(manager.discover("remote-test"))
    complete_admission(manager, 'remote-test')
    manager.set_enabled("remote-test", True, 3)
    assert manager.enabled_bindings() == [{"service_id": "remote-test", "revision": 4, "manifest": manifest()}]
    disabled_connection = manager.set_enabled("remote-test", False, 4)
    assert manager.get_binding("remote-test")["manifest"] == manifest()
    before = len(calls)
    with pytest.raises(RemoteServiceError) as disabled:
        run(manager.invoke("remote-test", disabled_connection['revision'], request()))
    assert disabled.value.code == "remote_disabled" and len(calls) == before


@pytest.mark.parametrize("revision", [True, False, 0, -1, 1.0, "1", None])
def test_supplied_save_revision_is_a_strict_positive_integer(tmp_path, revision):
    manager = RemoteServiceManager(tmp_path)
    manager.save_connection(connection(token=TOKEN))
    before = manager.path.read_bytes()
    with pytest.raises(RemoteServiceError) as error:
        manager.save_connection({"id": "remote-test", "expected_revision": revision, "name": "不应保存"})
    assert error.value.code == "invalid_remote_config"
    assert manager.path.read_bytes() == before
    assert TOKEN not in error.value.message


def test_save_revision_is_checked_atomically_and_not_persisted_or_exposed(tmp_path):
    manager = RemoteServiceManager(tmp_path)
    original = manager.save_connection(connection(token=TOKEN))
    updated = manager.save_connection({"id": "remote-test", "expected_revision": original['revision'], "name": "新名称"})
    assert updated['revision'] == original['revision'] + 1
    before = manager.path.read_bytes()
    for values in ({"name": "覆盖新名称"}, {"name": "新名称"}, {"clear_token": True}):
        with pytest.raises(RemoteServiceError) as error:
            manager.save_connection({"id": "remote-test", "expected_revision": original['revision'], **values})
        assert error.value.code == 'remote_revision_changed'
        assert manager.path.read_bytes() == before
    unchanged = manager.save_connection({"id": "remote-test", "expected_revision": updated['revision'], "name": "新名称"})
    assert unchanged == updated
    assert 'expected_revision' not in manager.path.read_text('utf-8')
    assert 'expected_revision' not in json.dumps(manager.list_services())
    assert TOKEN not in json.dumps(manager.list_services())


def test_supplied_revision_cannot_recreate_missing_connection(tmp_path):
    manager = RemoteServiceManager(tmp_path)
    with pytest.raises(RemoteServiceError) as error:
        manager.save_connection(connection(expected_revision=1))
    assert error.value.code == 'remote_revision_changed'
    assert manager.list_services() == {'items': []}


def test_simultaneous_connection_edits_with_one_revision_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    manager = RemoteServiceManager(tmp_path)
    manager.save_connection(connection())
    start = Barrier(2)
    def edit(name):
        start.wait(timeout=2)
        try:
            return manager.save_connection({'id': 'remote-test', 'expected_revision': 1, 'name': name})
        except RemoteServiceError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['并发名称甲', '并发名称乙']))
    assert sum(isinstance(value, dict) and value['revision'] == 2 for value in results) == 1
    assert results.count('remote_revision_changed') == 1
    assert manager.list_services()['items'][0]['revision'] == 2


def test_http_save_policy_requires_revision_inside_lock_and_keeps_trusted_compatibility(tmp_path):
    manager = RemoteServiceManager(tmp_path)
    saved = manager.save_connection(connection(), require_revision=True)
    assert saved['revision'] == 1
    before = manager.path.read_bytes()
    with pytest.raises(RemoteServiceError) as missing:
        manager.save_connection(connection(name='不能作为再次创建覆盖'), require_revision=True)
    assert missing.value.code == 'remote_revision_required'
    assert manager.path.read_bytes() == before
    updated = manager.save_connection({'id': 'remote-test', 'expected_revision': 1, 'name': '合法改名'}, require_revision=True)
    assert updated['revision'] == 2
    trusted = manager.save_connection({'id': 'remote-test', 'name': '受信维护兼容'})
    assert trusted['revision'] == 3


def test_simultaneous_http_creates_with_same_id_cannot_overwrite_the_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    manager = RemoteServiceManager(tmp_path)
    start = Barrier(2)
    def create(name):
        start.wait(timeout=2)
        try:
            return manager.save_connection(connection(name=name), require_revision=True)
        except RemoteServiceError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ['新建名称甲', '新建名称乙']))
    assert sum(isinstance(value, dict) and value['revision'] == 1 for value in results) == 1
    assert results.count('remote_revision_required') == 1
    assert manager.list_services()['items'][0]['revision'] == 1


def test_repeated_publication_is_noop_after_validating_current_revision(tmp_path, monkeypatch):
    manager, _ = ready(tmp_path)
    disabled = manager.set_enabled('remote-test', False, 2)
    def unexpected_write(_):
        raise AssertionError('相同状态不应重写连接文件')
    original_write = manager._write
    monkeypatch.setattr(manager, '_write', unexpected_write)
    assert not manager.set_enabled('remote-test', False, disabled['revision'])['enabled']
    for stale in (0, True, 1.0, '1', 2):
        with pytest.raises(RemoteServiceError) as error:
            manager.set_enabled('remote-test', False, stale)
        assert error.value.code == 'remote_revision_changed'
    monkeypatch.setattr(manager, '_write', original_write)
    enabled = manager.set_enabled('remote-test', True, disabled['revision'])
    monkeypatch.setattr(manager, '_write', unexpected_write)
    assert manager.set_enabled('remote-test', True, enabled['revision'])['enabled']


def test_discovery_rechecks_credentials_added_while_waiting(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        pending_token = 'synthetic-new-pending-credential-987654'
        response = manifest()
        response['module']['description'] = pending_token
        async def handler(_):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=response)
        manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(handler))
        manager.save_connection(connection())
        pending = asyncio.create_task(manager.discover('remote-test'))
        await entered.wait()
        manager.save_connection(connection(id='another', token=pending_token))
        release.set()
        with pytest.raises(RemoteServiceError) as error:
            await pending
        assert error.value.code == 'remote_secret_echo'
        assert pending_token not in error.value.message
        assert manager.get_binding('remote-test')['manifest'] is None
        assert pending_token not in json.dumps(manager.list_services())
        assert pending_token not in manager.path.read_text('utf-8')
    run(scenario())


def test_discovery_before_commit_can_reject_revoked_management_permission(tmp_path):
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=manifest())))
    manager.save_connection(connection())
    original = manager.path.read_bytes()
    rejection = RuntimeError('合成授权撤销')
    def revoked():
        raise rejection
    with pytest.raises(RuntimeError) as error:
        run(manager.discover('remote-test', before_commit=revoked))
    assert error.value is rejection
    assert manager.path.read_bytes() == original
    assert manager.get_binding('remote-test')['manifest'] is None


def test_disable_reenable_invalidates_inflight_result_and_old_management_revision(tmp_path):
    manager = None
    def mutate(_):
        disabled = manager.set_enabled('remote-test', False, 2)
        enabled = manager.set_enabled('remote-test', True, disabled['revision'])
        assert enabled['revision'] == 4
        return httpx.Response(200, json=completed())
    manager, calls = ready(tmp_path, mutate)
    with pytest.raises(RemoteServiceError) as stale_result:
        run(manager.invoke('remote-test', 2, request()))
    assert stale_result.value.code == 'remote_revision_changed'
    assert len(calls) == 2
    with pytest.raises(RemoteServiceError) as stale_action:
        manager.set_enabled('remote-test', False, 2)
    assert stale_action.value.code == 'remote_revision_changed'
    assert manager.list_services()['items'][0]['enabled']


def test_failed_publication_write_does_not_move_health_or_revision(tmp_path, monkeypatch):
    manager, _ = ready(tmp_path)
    run(manager.health('remote-test'))
    original_file = manager.path.read_bytes()
    original_health = manager.monitor_status('remote-test')
    def fail(_):
        raise RemoteServiceError('remote_config_write_failed', '合成保存失败')
    monkeypatch.setattr(manager, '_write', fail)
    with pytest.raises(RemoteServiceError) as error:
        manager.set_enabled('remote-test', False, 2)
    assert error.value.code == 'remote_config_write_failed'
    assert manager.path.read_bytes() == original_file
    assert manager.get_binding('remote-test')['revision'] == 2
    assert manager.monitor_status('remote-test') == original_health


def test_destination_change_needs_new_token_or_explicit_clear(tmp_path):
    manager, calls = ready(tmp_path)
    original = manager.path.read_bytes()
    with pytest.raises(RemoteServiceError) as error:
        manager.save_connection({"id": "remote-test", "base_url": "https://changed.example", "token": ""})
    assert error.value.code == "remote_destination_changed" and manager.path.read_bytes() == original
    replacement = "synthetic-new-remote-token-123456"
    saved = manager.save_connection({"id": "remote-test", "base_url": "https://changed.example", "token": replacement})
    assert saved["revision"] == 3 and not saved["enabled"]
    run(manager.health("remote-test"))
    assert calls[-1].url.host == "changed.example" and calls[-1].headers["Authorization"] == "Bearer " + replacement
    cleared = manager.save_connection({"id": "remote-test", "base_url": "http://localhost:8021", "clear_token": True})
    assert cleared["revision"] == 4 and not cleared["token_configured"]
    run(manager.health("remote-test"))
    assert "Authorization" not in calls[-1].headers


def test_tokens_cannot_leak_via_metadata_or_other_connection(tmp_path):
    manager = RemoteServiceManager(tmp_path)
    manager.save_connection(connection(token=TOKEN))
    with pytest.raises(RemoteServiceError):
        manager.save_connection(connection(id="other", name=TOKEN))
    manager.save_connection(connection(id="other", name="new-token-placeholder"))
    with pytest.raises(RemoteServiceError):
        manager.save_connection({"id": "remote-test", "token": "new-token-placeholder"})
    assert TOKEN not in json.dumps(manager.list_services())


def test_http_contract_has_stable_ids_minimal_context_and_only_fixed_endpoints(tmp_path):
    manager, calls = ready(tmp_path)
    first = run(manager.invoke("remote-test", 2, request()))
    second = run(manager.invoke("remote-test", 2, request()))
    assert first == second == completed()
    assert str(calls[0].url) == "https://service.example/prefix/platform/v1/manifest"
    for call in calls[1:]:
        assert str(call.url) == "https://service.example/prefix/platform/v1/invoke"
        assert call.headers["Authorization"] == "Bearer " + TOKEN
        assert json.loads(call.content) == request()
        assert call.extensions["timeout"]["read"] == 12
    # Retry is explicit at this layer and preserves the caller's stable request ID.
    assert len(calls) == 3


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(protocol_version="2.0"),
    lambda m: m.update(handler="evil.py:run"),
    lambda m: m["capabilities"][0].update(handler="evil.py:run"),
    lambda m: m["capabilities"][0].update(url="https://arbitrary.example/invoke"),
    lambda m: m["capabilities"][0].update(views=[]),
    lambda m: m["capabilities"][0]["presentation"]["page"].update(renderer="resource"),
    lambda m: m["capabilities"][0]["execution"].update(mode="sync"),
    lambda m: m["capabilities"][0]["input_schema"].update({"$ref": "https://arbitrary.example/schema"}),
    lambda m: m["capabilities"][0]["output_schema"].update({"$dynamicRef": "file:///private"}),
    lambda m: m["capabilities"][0]["input_schema"].update(type="bogus-type"),
    lambda m: m["capabilities"][0].update(roles=["root"]),
    lambda m: m["capabilities"][0]["result"]["text_artifact"].update(name="../outside.txt"),
    lambda m: m["capabilities"].append(deepcopy(m["capabilities"][0])),
])
def test_untrusted_manifest_cannot_add_code_urls_renderers_or_nonlocal_refs(tmp_path, mutation):
    payload = manifest()
    mutation(payload)
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    manager.save_connection(connection())
    before = manager.path.read_bytes()
    with pytest.raises(RemoteServiceError) as error:
        run(manager.discover("remote-test"))
    assert error.value.code == "remote_invalid_manifest"
    assert manager.path.read_bytes() == before


def test_discovery_preserves_equal_contract_and_disables_changed_contract_with_new_revision(tmp_path):
    payload = manifest()
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    manager.save_connection(connection(token=TOKEN))
    first = run(manager.discover("remote-test"))
    complete_admission(manager, 'remote-test')
    manager.set_enabled("remote-test", True, first["revision"])
    same = run(manager.discover("remote-test"))
    assert same["revision"] == 2 and same["enabled"]
    payload["capabilities"][0]["version"] = "1.1.0"
    changed = run(manager.discover("remote-test"))
    assert changed["revision"] == changed["discovered_revision"] == 3 and not changed["enabled"]
    assert changed["manifest"] == payload and not manager.enabled_bindings()
    with pytest.raises(RemoteServiceError) as stale:
        run(manager.invoke("remote-test", 2, request()))
    assert stale.value.code == "remote_revision_changed"


def test_failed_discovery_preserves_previously_published_manifest(tmp_path):
    payload = manifest()
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    manager.save_connection(connection(token=TOKEN))
    run(manager.discover("remote-test"))
    complete_admission(manager, 'remote-test')
    manager.set_enabled("remote-test", True, 1)
    before = manager.path.read_bytes()
    payload["module"]["handler"] = "not-allowed"
    with pytest.raises(RemoteServiceError):
        run(manager.discover("remote-test"))
    assert manager.path.read_bytes() == before and len(manager.enabled_bindings()) == 1


def test_discovery_cannot_publish_a_response_after_connection_changes(tmp_path):
    manager = None

    def handler(req):
        manager.save_connection({"id": "remote-test", "timeout_seconds": 13})
        return httpx.Response(200, json=manifest())

    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(handler))
    manager.save_connection(connection())
    with pytest.raises(RemoteServiceError) as stale:
        run(manager.discover("remote-test"))
    assert stale.value.code == "remote_revision_changed"
    assert manager.get_binding("remote-test")["manifest"] is None


@pytest.mark.parametrize("status,code", [(202, "remote_async_unsupported"), (307, "remote_redirect_blocked"),
                                        (401, "remote_auth_failed"), (403, "remote_auth_failed"),
                                        (409, "remote_idempotency_conflict"), (429, "remote_rate_limited"), (500, "remote_service_failed")])
def test_service_failure_bodies_and_redirect_credentials_never_escape(tmp_path, status, code):
    manager, calls = ready(tmp_path, lambda req: httpx.Response(status, text="private-body " + TOKEN,
                                                             headers={"Location": "https://attacker.example/steal"}))
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request()))
    assert error.value.code == code
    assert TOKEN not in str(error.value) and "private-body" not in str(error.value)
    assert len(calls) == 2 and calls[-1].url.host == "service.example"


@pytest.mark.parametrize("status", [501, 502, 503, 504])
@pytest.mark.parametrize("code", ["ai_provider_limited", "ai_provider_auth", "ai_provider_unavailable",
                                  "ai_platform_rejected", "ai_disabled", "model_timeout"])
def test_known_ai_failure_codes_use_static_safe_diagnostics(tmp_path, status, code):
    secret = "canary-private-provider-key"
    manager, calls = ready(tmp_path, lambda req: httpx.Response(status, json={
        "error": {"code": code, "message": "malicious private body " + secret + TOKEN,
                  "extra": {"api_key": secret}}
    }))
    with pytest.raises(RemoteServiceError) as caught:
        run(manager.invoke("remote-test", 2, request()))
    assert caught.value.code == "remote_" + code
    assert caught.value.message == manager._AI_FAILURES[code]
    assert secret not in str(caught.value) and TOKEN not in str(caught.value)
    assert "malicious" not in str(caught.value) and len(calls) == 2


@pytest.mark.parametrize("status", [501, 502, 503, 504])
@pytest.mark.parametrize("payload", [
    {"error": {"code": "unknown_canary_secret", "message": "private-canary"}},
    {"error": {"code": ["ai_provider_limited"], "message": "private-canary"}},
    {"error": {"message": "ai_provider_limited private-canary"}},
    {"code": "ai_provider_limited", "message": "private-canary"},
])
def test_unknown_or_malformed_ai_failure_keeps_existing_generic_behavior(tmp_path, status, payload):
    manager, calls = ready(tmp_path, lambda req: httpx.Response(status, json=payload))
    with pytest.raises(RemoteServiceError) as caught:
        run(manager.invoke("remote-test", 2, request()))
    assert caught.value.code == "remote_service_failed"
    assert "private-canary" not in str(caught.value) and "unknown_canary_secret" not in str(caught.value)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 404, 413, 422])
def test_ai_diagnostic_code_does_not_override_existing_auth_or_validation_status(tmp_path, status):
    manager, _ = ready(tmp_path, lambda req: httpx.Response(status, json={
        "error": {"code": "ai_provider_limited", "message": "private-canary"}
    }))
    with pytest.raises(RemoteServiceError) as caught:
        run(manager.invoke("remote-test", 2, request()))
    assert caught.value.code == ("remote_auth_failed" if status in {401, 403} else "remote_service_failed")


def test_ai_failure_selector_body_limit_rejects_partial_known_code(tmp_path):
    payload = {"error": {"code": "ai_provider_limited", "message": "private-canary " + "x" * 5000}}
    manager, calls = ready(tmp_path, lambda req: httpx.Response(502, json=payload))
    with pytest.raises(RemoteServiceError) as caught:
        run(manager.invoke("remote-test", 2, request()))
    assert caught.value.code == "remote_service_failed"
    assert "private-canary" not in str(caught.value) and len(calls) == 2


def test_non_invoke_errors_cannot_select_ai_diagnostic_wording(tmp_path):
    manager, _ = ready(tmp_path)
    manager._transport = httpx.MockTransport(lambda req: httpx.Response(502, json={
        "error": {"code": "ai_provider_limited", "message": "private-canary"}
    }))
    item, token, _ = manager._snapshot("remote-test")
    with pytest.raises(RemoteServiceError) as caught:
        run(manager._request(item, token, "GET", "health"))
    assert caught.value.code == "remote_service_failed"


@pytest.mark.parametrize("remote_code,expected", [
    ("ai_disabled", "remote_ai_disabled"),
    ("ai_platform_rejected", "remote_ai_platform_rejected"),
    ("revision_conflict", "remote_revision_conflict"),
    ("state_conflict", "remote_state_conflict"),
    ("idempotency_conflict", "remote_idempotency_conflict"),
    ("unknown-private-canary", "remote_conflict"),
])
def test_modern_409_preserves_existing_business_conflicts_and_safe_ai_codes(tmp_path, remote_code, expected):
    manager, _ = ready(tmp_path)
    manager._transport = httpx.MockTransport(lambda req: httpx.Response(409, json={
        "error": {"code": remote_code, "message": "private-canary " + TOKEN}
    }))
    item, token, _ = manager._snapshot("remote-test")
    with pytest.raises(RemoteServiceError) as caught:
        run(manager._request(item, token, "POST", "invoke", body={"protocol_version": "1.1"}))
    assert caught.value.code == expected
    assert TOKEN not in str(caught.value) and "private-canary" not in str(caught.value)


@pytest.mark.parametrize("status,expected", [(403, "remote_forbidden"), (404, "remote_not_found"),
                                           (413, "remote_input_too_large"), (422, "remote_validation"),
                                           (503, "remote_unavailable")])
def test_modern_unknown_error_code_retains_original_http_business_meaning(tmp_path, status, expected):
    manager, _ = ready(tmp_path)
    manager._transport = httpx.MockTransport(lambda req: httpx.Response(status, json={
        "error": {"code": "unknown-private-canary", "message": TOKEN}
    }))
    item, token, _ = manager._snapshot("remote-test")
    with pytest.raises(RemoteServiceError) as caught:
        run(manager._request(item, token, "POST", "invoke", body={"protocol_version": "1.1"}))
    assert caught.value.code == expected and TOKEN not in str(caught.value)


def test_ai_failure_selector_decoding_has_its_own_deadline(tmp_path):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"error":{"code":"ai_provider_limited",'
            await asyncio.sleep(5)
            yield b'"message":"private-canary"}}'

    manager, calls = ready(tmp_path, lambda req: httpx.Response(502, stream=SlowBody()))
    with pytest.raises(RemoteServiceError) as caught:
        run(manager.invoke("remote-test", 2, request()))
    assert caught.value.code == "remote_service_failed" and len(calls) == 2
    assert "private-canary" not in str(caught.value)


def test_timeout_is_safe_and_not_retried(tmp_path):
    def timeout(req):
        raise httpx.ReadTimeout(TOKEN, request=req)

    manager, calls = ready(tmp_path, timeout)
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request()))
    assert error.value.code == "remote_timeout" and TOKEN not in str(error.value)
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [{"request_id": "another-invocation"}, {"capability_id": "different.cap"},
                                    {"capability_version": "wrong"}, {"protocol_version": "2.0"},
                                    {"views": []}, {"resource": {"id": "forged"}}, {"data": []}, {"summary": None}])
def test_response_must_match_exact_envelope_and_cannot_inject_platform_objects(tmp_path, changes):
    manager, _ = ready(tmp_path, lambda req: httpx.Response(200, json=completed(**changes)))
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request()))
    assert error.value.code == "remote_invalid_response"


def test_exact_token_echo_is_redacted_from_returned_nested_data_and_summary(tmp_path):
    manager, _ = ready(tmp_path, lambda req: httpx.Response(200, json=completed(summary=TOKEN, data={TOKEN: ["value=" + TOKEN]})))
    result = run(manager.invoke("remote-test", 2, request()))
    assert TOKEN not in json.dumps(result)
    assert "凭据已隐藏" in result["summary"]


def test_known_token_in_request_is_blocked_before_network(tmp_path):
    manager, calls = ready(tmp_path)
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request(input={"text": TOKEN})))
    assert error.value.code == "remote_secret_in_input" and len(calls) == 1


@pytest.mark.parametrize("change", ["disable", "revision"])
def test_configuration_revocation_while_invocation_waits_blocks_publication(tmp_path, change):
    manager = None

    def mutate(req):
        if change == "disable":
            manager.set_enabled("remote-test", False, 2)
        else:
            manager.save_connection({"id": "remote-test", "timeout_seconds": 15})
        return httpx.Response(200, json=completed())

    manager, calls = ready(tmp_path, mutate)
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request()))
    assert error.value.code == "remote_revision_changed"
    assert len(calls) == 2


def test_health_returns_local_metadata_and_never_service_secret_echo(tmp_path):
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "ok", "message": TOKEN, "data": {"private": TOKEN}})))
    manager.save_connection(connection(token=TOKEN))
    result = run(manager.health("remote-test"))
    assert result["ok"] and set(result) == {"ok", "message", "latency_ms"}
    assert TOKEN not in json.dumps(result) and TOKEN not in manager.path.read_text("utf-8")
    assert manager.list_services()["items"][0]["last_health"] == result


@pytest.mark.parametrize("body,expected", [(b"non-JSON private", "remote_invalid_json"), (b'{"data":NaN}', "remote_invalid_json")])
def test_invalid_json_is_safe(tmp_path, body, expected):
    manager, _ = ready(tmp_path, lambda req: httpx.Response(200, content=body))
    with pytest.raises(RemoteServiceError) as error:
        run(manager.invoke("remote-test", 2, request()))
    assert error.value.code == expected and body.decode() not in str(error.value)


def test_http_response_and_request_sizes_are_bounded(tmp_path):
    manager, calls = ready(tmp_path, lambda req: httpx.Response(200, content=b"x" * 1025))
    manager._MAX_RESPONSE = 1024
    with pytest.raises(RemoteServiceError) as large_response:
        run(manager.invoke("remote-test", 2, request()))
    assert large_response.value.code == "remote_response_too_large"
    manager._MAX_REQUEST = 128
    before = len(calls)
    with pytest.raises(RemoteServiceError) as large_request:
        run(manager.invoke("remote-test", 2, request(input={"text": "x" * 256})))
    assert large_request.value.code == "remote_request_too_large" and len(calls) == before


def test_corrupt_persisted_config_fails_closed_without_exposing_content(tmp_path):
    manager = RemoteServiceManager(tmp_path)
    manager.path.write_text(TOKEN, "utf-8")
    with pytest.raises(RemoteServiceError) as error:
        manager.list_services()
    assert error.value.code == "remote_config_corrupt" and TOKEN not in str(error.value)


def test_result_title_from_input_is_optional_bounded_and_preserved_in_discovery(tmp_path):
    payload = manifest()
    payload["capabilities"][0]["result"]["title_from_input"] = "title"
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=payload)))
    manager.save_connection(connection())
    discovered = run(manager.discover("remote-test"))
    assert discovered["manifest"]["capabilities"][0]["result"]["title_from_input"] == "title"
    assert RemoteServiceManager(tmp_path).get_binding("remote-test")["manifest"] == payload
    original = manager.path.read_bytes()
    for invalid_field in ("", "   ", "x" * 121, 12):
        payload["capabilities"][0]["result"]["title_from_input"] = invalid_field
        with pytest.raises(RemoteServiceError) as error:
            run(manager.discover("remote-test"))
        assert error.value.code == "remote_invalid_manifest"
        assert manager.path.read_bytes() == original


@pytest.mark.parametrize("recovery", ["replace", "clear"])
def test_two_unreadable_old_credentials_can_be_repaired_one_at_a_time_without_weakening_execution(tmp_path, monkeypatch, recovery):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/manifest"):
            return httpx.Response(200, json=manifest())
        return httpx.Response(200, json=completed(json.loads(req.content)))

    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(handler))
    for service_id in ("first", "second"):
        manager.save_connection(connection(id=service_id, token=f"synthetic-previous-identity-{service_id}"))
    old_secrets = [deepcopy(item["secret"]) for item in manager._read()["services"]]
    decrypt = manager._tokens.decrypt

    def previous_identity_unavailable(secret):
        if secret in old_secrets:
            raise RemoteServiceError("remote_secret_unavailable", "合成旧身份凭据不可解密。")
        return decrypt(secret)

    monkeypatch.setattr(manager._tokens, "decrypt", previous_identity_unavailable)
    before = manager.path.read_bytes()
    with pytest.raises(RemoteServiceError):
        manager.save_connection({"id": "first", "name": "普通改名不绕过检查"})
    assert manager.path.read_bytes() == before
    with pytest.raises(RemoteServiceError):
        manager.redact("合成业务输入")

    def repair(service_id):
        operation = {"token": f"synthetic-new-identity-{service_id}"} if recovery == "replace" else {"clear_token": True}
        return manager.save_connection({"id": service_id, **operation})

    repaired = repair("first")
    assert not repaired["enabled"] and repaired["revision"] == 2
    assert repaired["token_configured"] is (recovery == "replace")
    assert calls == []
    # A second unreadable token still closes ordinary business/redaction paths.
    with pytest.raises(RemoteServiceError) as still_unavailable:
        manager.redact("合成业务输入")
    assert still_unavailable.value.code == "remote_secret_unavailable"
    with pytest.raises(RemoteServiceError) as invoke_blocked:
        run(manager.invoke("first", repaired["revision"], request()))
    assert invoke_blocked.value.code == "remote_secret_unavailable" and calls == []
    repair("second")
    assert manager.redact("合成业务输入") == "合成业务输入"
    if recovery == "replace":
        assert "synthetic-new-identity-first" not in manager.redact("synthetic-new-identity-first")
    discovered = run(manager.discover("first"))
    if recovery == 'clear':
        result = run(manager.admit('first', discovered['revision']))
        assert not result['admission']['passed']
        with pytest.raises(RemoteServiceError) as blocked:
            manager.set_enabled('first', True, discovered['revision'])
        assert blocked.value.code == 'remote_admission_required'
        return
    complete_admission(manager, 'first')
    enabled = manager.set_enabled("first", True, discovered["revision"])
    assert run(manager.invoke("first", enabled["revision"], request()))["data"] == {"text": "合成输出"}
    expected = "Bearer synthetic-new-identity-first" if recovery == "replace" else None
    assert calls[-1].headers.get("Authorization") == expected
