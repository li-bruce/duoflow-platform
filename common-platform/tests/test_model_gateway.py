"""HTTP-mocked security and behavior checks; never use real provider credentials."""

import asyncio
import json
import os

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app import model_gateway as module
from app.model_gateway import ModelGateway, ModelGatewayError
from app.model_routes import create_model_router


SECRET = "synthetic-secret-DO-NOT-USE-123456"


def profile(**overrides):
    return {"name": "Synthetic model", "base_url": "https://models.example.test/v1",
            "model": "test-model", "enabled": True, **overrides}


def completed(content="Hello", **extra):
    return {"choices": [{"message": {"content": content, **extra}}], "model": "test-model",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}


def run(coroutine):
    return asyncio.run(coroutine)


def test_save_never_calls_provider_and_secrets_are_encrypted(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completed())

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    result = gateway.save_profile(profile(api_key=SECRET))
    assert calls == []
    assert result["api_key_configured"] is True
    assert SECRET not in json.dumps(gateway.list_profiles())
    assert "ciphertext" not in json.dumps(gateway.list_profiles())
    assert all(SECRET.encode() not in path.read_bytes() for path in tmp_path.iterdir() if path.is_file())
    restarted = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    assert restarted.configured
    assert run(restarted.chat([{"role": "user", "content": "Hello"}]))["content"] == "Hello"
    assert calls[0].headers["authorization"] == "Bearer " + SECRET
    assert restarted.secrets_storage in {"windows-dpapi", "fernet-local-file"}


def test_empty_key_preserves_replace_changes_and_clear_removes(tmp_path):
    headers = []

    def handler(request):
        headers.append(request.headers.get("authorization"))
        return httpx.Response(200, json=completed())

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    saved = gateway.save_profile(profile(api_key=SECRET))
    gateway.save_profile({"id": saved["id"], "api_key": "", "name": "Renamed"})
    run(gateway.chat([{"role": "user", "content": "hello"}]))
    replacement = "synthetic-rotated-secret-00000"
    gateway.save_profile({"id": saved["id"], "api_key": replacement})
    run(gateway.chat([{"role": "user", "content": "hello"}]))
    cleared = gateway.save_profile({"id": saved["id"], "clear_api_key": True})
    run(gateway.chat([{"role": "user", "content": "hello"}]))
    assert headers == ["Bearer " + SECRET, "Bearer " + replacement, None]
    assert not cleared["api_key_configured"]


def test_default_disable_delete_and_member_metadata(tmp_path):
    gateway = ModelGateway(tmp_path)
    first = gateway.save_profile(profile(api_key=SECRET))
    second = gateway.save_profile(profile(name="Second", is_default=True))
    assert gateway.list_profiles()["default_profile_id"] == second["id"]
    gateway.save_profile({"id": second["id"], "enabled": False})
    assert gateway.list_profiles()["default_profile_id"] == first["id"]
    public = gateway.list_profiles(admin=False)
    assert len(public["items"]) == 1
    assert set(public["items"][0]) == {"id", "name", "model", "is_default"}
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hi"}], second["id"]))
    assert captured.value.code == "model_disabled"
    gateway.delete_profile(first["id"])
    assert not gateway.configured
    assert gateway.list_profiles()["default_profile_id"] is None
    gateway.save_profile({"id": second["id"], "enabled": True})
    assert gateway.list_profiles()["default_profile_id"] == second["id"]
    gateway.delete_profile(second["id"])
    with pytest.raises(ModelGatewayError, match="尚未配置"):
        run(gateway.chat([{"role": "user", "content": "hi"}]))
    with pytest.raises(ModelGatewayError):
        gateway.delete_profile(second["id"])


@pytest.mark.parametrize("url", ["http://localhost:9000/v1", "http://127.0.0.1:8000/v1/",
                                "http://[::1]:8080/v1", "https://provider.example/v1"])
def test_allowed_explicit_urls(tmp_path, url):
    saved = ModelGateway(tmp_path).save_profile(profile(base_url=url))
    assert saved["base_url"] == url.rstrip("/")


@pytest.mark.parametrize("url", ["ftp://provider.example/v1", "http://provider.example/v1",
                                "https://user:secret@provider.example/v1", "https://provider.example/v1?key=secret",
                                "https://provider.example/v1#secret", "https://provider.example/v1?",
                                "https://provider.example\\@localhost/v1", "https://provider.example/\npath",
                                "https://provider.example:99999/v1", "https://user%40provider.example/v1"])
def test_reject_unsafe_urls_without_echo(tmp_path, url):
    with pytest.raises(ModelGatewayError) as captured:
        ModelGateway(tmp_path).save_profile(profile(base_url=url))
    assert url not in str(captured.value)


def test_real_request_shape_tools_and_tool_result(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=completed(None, tool_calls=[
                {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"id":"a"}'}}]))
        return httpx.Response(200, json=completed("The tool returned 3."))

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    gateway.save_profile(profile(max_tokens=777, temperature=0.4, timeout_seconds=12))
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    initial = run(gateway.chat([{"role": "user", "content": "look up"}], tools=tools))
    assert initial["content"] == ""
    assert initial["tool_calls"][0]["function"]["name"] == "lookup"
    followup = [{"role": "user", "content": "look up"},
                {"role": "assistant", "content": None, "tool_calls": initial["tool_calls"]},
                {"role": "tool", "tool_call_id": "call_1", "content": "3"}]
    assert run(gateway.chat(followup))["content"] == "The tool returned 3."
    body = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://models.example.test/v1/chat/completions"
    assert body["tools"] == tools and body["stream"] is False
    assert body["temperature"] == 0.4 and body["max_tokens"] == 777
    assert requests[0].extensions["timeout"]["read"] == 12
    assert json.loads(requests[1].content)["messages"][-1]["role"] == "tool"


@pytest.mark.parametrize("finish_reason", ["stop", "length", "max_tokens", "tool_calls",
                                          "content_filter", "function_call"])
def test_finish_reason_preserves_safe_provider_enum_only(tmp_path, finish_reason):
    response = completed("Synthetic reply")
    response["choices"][0].update({"finish_reason": finish_reason,
                                   "provider_diagnostic": "private diagnostic " + SECRET})
    response["choices"][0]["message"]["reasoning_content"] = "private reasoning " + SECRET
    response["model"] = "provider echo " + SECRET
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)))
    gateway.save_profile(profile(api_key=SECRET))

    reply = run(gateway.chat([{"role": "user", "content": "Synthetic request"}]))

    assert reply["finish_reason"] == finish_reason
    assert reply["content"] == "Synthetic reply"
    assert reply["model"] == "provider echo 【密钥已隐藏】"
    assert set(reply) == {"content", "tool_calls", "model", "finish_reason", "usage"}
    assert SECRET not in json.dumps(reply)
    assert "private diagnostic" not in json.dumps(reply)
    assert "private reasoning" not in json.dumps(reply)


@pytest.mark.parametrize("finish_reason", [None, True, 123, "LENGTH", " length ",
                                          "provider diagnostic " + SECRET,
                                          ["length", SECRET], {SECRET: "length"}])
def test_unknown_finish_reason_is_omitted_without_leaking_provider_data(tmp_path, finish_reason):
    response = completed()
    response["choices"][0]["finish_reason"] = finish_reason
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)))
    gateway.save_profile(profile(api_key=SECRET))

    reply = run(gateway.chat([{"role": "user", "content": "Synthetic request"}]))

    assert reply == {"content": "Hello", "tool_calls": [], "model": "test-model",
                     "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
    assert SECRET not in json.dumps(reply)


def test_absent_finish_reason_does_not_infer_completion_from_content(tmp_path):
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=completed('An incomplete JSON: {"value":'))))
    gateway.save_profile(profile())

    reply = run(gateway.chat([{"role": "user", "content": "Synthetic request"}]))

    assert "finish_reason" not in reply
    assert reply["content"] == 'An incomplete JSON: {"value":'


def test_finish_reason_cannot_echo_a_matching_credential(tmp_path):
    response = completed()
    response["choices"][0]["finish_reason"] = "length"
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)))
    gateway.save_profile(profile(api_key="length"))

    reply = run(gateway.chat([{"role": "user", "content": "Synthetic request"}]))

    assert "finish_reason" not in reply
    assert "length" not in json.dumps(reply)


@pytest.mark.parametrize("status,code", [(401, "model_auth_failed"), (403, "model_auth_failed"),
                                       (429, "model_rate_limited"), (500, "model_provider_error")])
def test_provider_error_bodies_and_credentials_never_escape(tmp_path, status, code):
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text="Authorization: Bearer " + SECRET)))
    gateway.save_profile(profile(api_key=SECRET))
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hi"}]))
    assert captured.value.code == code
    assert SECRET not in str(captured.value)
    assert "Authorization" not in str(captured.value)


def test_timeout_is_safe(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout(SECRET, request=request)

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    gateway.save_profile(profile(api_key=SECRET))
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hi"}]))
    assert captured.value.code == "model_timeout" and SECRET not in str(captured.value)


@pytest.mark.parametrize("body,code", [("not JSON " + SECRET, "model_invalid_json"),
                                     ('{"choices":[]}', "model_invalid_response"),
                                     ('{"choices":[{"message":{"content":5}}]}', "model_invalid_response")])
def test_invalid_provider_payload_is_safe(tmp_path, body, code):
    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    gateway.save_profile(profile(api_key=SECRET))
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hi"}]))
    assert captured.value.code == code and SECRET not in str(captured.value)


def test_redirect_never_forwards_authorization(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://attacker.example/steal"}, text=SECRET)

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    gateway.save_profile(profile(api_key=SECRET))
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hi"}]))
    assert captured.value.code == "model_redirect_blocked"
    assert seen == ["https://models.example.test/v1/chat/completions"]


def test_credentials_redacted_from_content_tools_model_and_outgoing_text(tmp_path):
    outbound = []

    def handler(request):
        outbound.append(json.loads(request.content))
        reply = completed("provider echo: " + SECRET, tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "echo", "arguments": json.dumps({"value": SECRET})}}])
        reply["model"] = SECRET
        return httpx.Response(200, json=reply)

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    gateway.save_profile(profile(api_key=SECRET))
    reply = run(gateway.chat([{"role": "user", "content": SECRET}]))
    assert SECRET not in json.dumps(reply)
    assert SECRET not in json.dumps(outbound)
    assert "【密钥已隐藏】" in reply["content"]
    assert gateway.redact("Token " + SECRET) == "Token 【密钥已隐藏】"
    with pytest.raises(ModelGatewayError):
        gateway.save_profile(profile(name="Oops " + SECRET))


def test_redaction_marker_and_metadata_cannot_reintroduce_a_credential(tmp_path):
    gateway = ModelGateway(tmp_path)
    saved = gateway.save_profile(profile(api_key="SECRET"))
    assert "SECRET" not in gateway.redact("Token SECRET")
    with pytest.raises(ModelGatewayError):
        gateway.save_profile({"id": saved["id"], "clear_api_key": True, "name": "SECRET"})
    with pytest.raises(ModelGatewayError):
        gateway.save_profile(profile(api_key="中文密钥"))
    second = gateway.save_profile(profile(name="provider-public-label"))
    with pytest.raises(ModelGatewayError):
        gateway.save_profile({"id": saved["id"], "api_key": second["name"]})


def test_total_timeout_also_covers_mocked_or_slow_streams(tmp_path):
    async def handler(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json=completed())

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    gateway.save_profile(profile(timeout_seconds=1))
    with pytest.raises(ModelGatewayError) as captured:
        run(gateway.chat([{"role": "user", "content": "hello"}]))
    assert captured.value.code == "model_timeout"


def test_connection_test_only_sends_synthetic_text(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completed("CONNECTION_OK"))

    gateway = ModelGateway(tmp_path, transport=httpx.MockTransport(handler))
    saved = gateway.save_profile(profile(api_key=SECRET))
    result = run(gateway.test_connection(saved["id"]))
    assert result["ok"] and result["latency_ms"] >= 0
    assert result["response_preview"] == "CONNECTION_OK"
    assert "synthetic connection test" in bodies[0]["messages"][0]["content"]
    assert SECRET not in json.dumps(bodies)


def test_fernet_fallback_and_missing_key_fail_closed(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("DPAPI not available")

    monkeypatch.setattr(module, "_dpapi", unavailable)
    gateway = ModelGateway(tmp_path)
    saved = gateway.save_profile(profile(api_key=SECRET))
    state = json.loads(gateway.path.read_text(encoding="utf-8"))
    assert state["profiles"][0]["secret"]["scheme"] == "fernet"
    assert gateway.secrets_storage == "fernet-local-file"
    assert SECRET not in gateway.path.read_text(encoding="utf-8")
    key_path = tmp_path / "model-profiles.fernet.key"
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600
    key_path.unlink()
    with pytest.raises(ModelGatewayError) as captured:
        gateway.redact("safe")
    assert captured.value.code == "model_secret_unavailable"
    assert not key_path.exists()  # Never silently replace a lost encryption key.
    assert not gateway.save_profile({"id": saved["id"], "clear_api_key": True})["api_key_configured"]


def test_explicit_replacement_can_recover_a_lost_fernet_key(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("DPAPI not available")

    monkeypatch.setattr(module, "_dpapi", unavailable)
    gateway = ModelGateway(tmp_path)
    saved = gateway.save_profile(profile(api_key=SECRET))
    (tmp_path / "model-profiles.fernet.key").unlink()
    replaced = gateway.save_profile({"id": saved["id"], "api_key": "synthetic-new-key-00000"})
    assert replaced["api_key_configured"]
    assert gateway.redact("synthetic-new-key-00000") == "【密钥已隐藏】"


def test_corrupt_config_not_overwritten(tmp_path):
    gateway = ModelGateway(tmp_path)
    gateway.path.write_text('{"api_key": "' + SECRET + '"}', encoding="utf-8")
    before = gateway.path.read_bytes()
    with pytest.raises(ModelGatewayError) as captured:
        gateway.save_profile(profile())
    assert captured.value.code == "model_config_corrupt" and SECRET not in str(captured.value)
    assert gateway.path.read_bytes() == before


def router_client(gateway):
    async def require_user(request: Request):
        role = request.headers.get("X-Test-Role")
        if role not in {"admin", "member"}:
            raise HTTPException(401)
        return {"id": role, "role": role}

    async def require_admin(request: Request):
        user = await require_user(request)
        if user["role"] != "admin":
            raise HTTPException(403)
        return user

    async def check_csrf(request: Request):
        if request.headers.get("X-CSRF-Token") != "test-csrf":
            raise HTTPException(403)

    app = FastAPI()
    app.include_router(create_model_router(gateway, require_admin, require_user, check_csrf), prefix="/api/v1")
    return TestClient(app)


def test_routes_protect_mutations_and_do_not_echo_keys(tmp_path):
    gateway = ModelGateway(tmp_path)
    client = router_client(gateway)
    admin = {"X-Test-Role": "admin", "X-CSRF-Token": "test-csrf"}
    member = {"X-Test-Role": "member", "X-CSRF-Token": "test-csrf"}
    assert client.get("/api/v1/models").status_code == 401
    assert client.post("/api/v1/models", json=profile(api_key=SECRET), headers=member).status_code == 403
    assert client.post("/api/v1/models", json=profile(api_key=SECRET), headers={"X-Test-Role": "admin"}).status_code == 403
    saved = client.post("/api/v1/models", json=profile(api_key=SECRET), headers=admin)
    assert saved.status_code == 200 and SECRET not in saved.text and "ciphertext" not in saved.text
    profile_id = saved.json()["id"]
    listing = client.get("/api/v1/models", headers=member)
    assert "api_key_configured" not in listing.text and "base_url" not in listing.text
    assert client.delete(f"/api/v1/models/{profile_id}", headers=member).status_code == 403
    assert client.post(f"/api/v1/models/{profile_id}/test", headers=member).status_code == 403
    invalid = client.post("/api/v1/models", json=profile(api_key=SECRET, temperature="invalid"), headers=admin)
    assert invalid.status_code == 422 and SECRET not in invalid.text
    malformed = client.post("/api/v1/models", content='{"api_key":"' + SECRET, headers=admin)
    assert malformed.status_code == 422 and SECRET not in malformed.text
    assert client.delete(f"/api/v1/models/{profile_id}", headers=admin).json()["ok"]
