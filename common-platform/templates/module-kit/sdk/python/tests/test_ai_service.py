"""Deterministic SDK transport tests. No real model or production identity used."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


spec = importlib.util.spec_from_file_location("portable_ai_service", Path(__file__).parents[1] / "ai_service.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
SERVICES = [dict(id="sample.ai", title="合成分析", description="SDK 回归", capability_ids=["sample.analyze"], supported_sources=["platform", "module"])]
PROFILE = dict(name="Synthetic provider", base_url="https://model.example/v1", model="synthetic-model",
               api_key="SYNTHETIC_PRIVATE_API_KEY_12345", enabled=True, temperature=0.2, max_tokens=32, timeout_seconds=2)
MESSAGES = [{"role": "user", "content": "合成输入"}]
SCOPE = dict(ticket="synthetic-access-ticket-value", request_id="a" * 32, operation="invoke", permission="sample.analyze", facts={"project_id": "P"})


def provider_result(content="synthetic answer"):
    return dict(model="synthetic-model", choices=[dict(message=dict(content=content), finish_reason="stop")], usage={"total_tokens": 8})


def configured(directory, handler=None, **kwargs):
    manager = sdk.AIServiceManager(services=SERVICES, data_dir=directory,
        transport=httpx.MockTransport(handler) if handler else None, **kwargs)
    manager.put_config("sample.ai", expected_revision=0, profile=PROFILE)
    return manager


def test_encrypted_restart_cas_clear_and_no_plaintext(tmp_path):
    manager = configured(tmp_path)
    config = manager.get_config("sample.ai")
    assert config["profile"]["api_key_configured"] is True
    assert PROFILE["api_key"] not in json.dumps(config)
    assert PROFILE["api_key"].encode() not in (tmp_path / "module-ai.config.enc").read_bytes()
    assert sdk.AIServiceManager(services=SERVICES, data_dir=tmp_path).get_config("sample.ai") == config
    with pytest.raises(sdk.AIServiceError, match="ai_config_revision_changed"):
        manager.put_config("sample.ai", expected_revision=0, profile=PROFILE)
    with pytest.raises(sdk.AIServiceError, match="ai_destination_changed"):
        manager.put_config("sample.ai", expected_revision=1, profile={"base_url": "https://other.example/v1"})
    with pytest.raises(sdk.AIServiceError, match="ai_configuration"):
        manager.put_config("sample.ai", expected_revision=1, profile={"name": PROFILE["api_key"]})
    assert manager.put_config("sample.ai", expected_revision=1, profile={"clear_api_key": True})["profile"]["api_key_configured"] is False


def test_standalone_own_api_redacts_outbound_and_inbound_key(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == PROFILE["base_url"] + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + PROFILE["api_key"]
        assert PROFILE["api_key"].encode() not in request.content
        return httpx.Response(200, json=provider_result("answer " + PROFILE["api_key"]))
    manager = configured(tmp_path, handler)
    receipt = asyncio.run(manager.chat_with_receipt(service_id="sample.ai", messages=[{"role": "user", "content": PROFILE["api_key"]}]))
    assert receipt["result"]["content"] == "answer 【密钥已隐藏】"
    assert receipt["route"]["binding_revision"] is None
    asyncio.run(manager.validate(service_id="sample.ai", route=receipt["route"]))
    manager.put_config("sample.ai", expected_revision=1, profile={"model": "new-model"})
    with pytest.raises(sdk.AIServiceError, match="ai_config_revision_changed"):
        asyncio.run(manager.validate(service_id="sample.ai", route=receipt["route"]))
    assert len(calls) == 1


@pytest.mark.parametrize("source", ["platform", "module"])
def test_connected_source_is_explicit_and_validated(tmp_path, source):
    calls = []
    route = dict(service_id="sample.ai", source=source, binding_revision=7, module_config_revision=1 if source == "module" else None,
                 platform_config_digest="f" * 64 if source == "platform" else None)
    def handler(request):
        calls.append(request.url.path)
        if request.url.host == "model.example":
            return httpx.Response(200, json=provider_result())
        assert request.headers["X-Platform-Access"] == SCOPE["ticket"]
        body = json.loads(request.content)
        assert body["facts"] == SCOPE["facts"]
        assert body["permission"] == SCOPE["permission"]
        answer = dict(route)
        if request.url.path.endswith("/chat"):
            answer["result"] = dict(content="platform answer", model="platform-model", tool_calls=[])
        return httpx.Response(200, json=answer)
    manager = configured(tmp_path, handler, platform_base_url="http://127.0.0.1:19000")
    receipt = asyncio.run(manager.chat_with_receipt(service_id="sample.ai", messages=MESSAGES, scope=SCOPE))
    assert receipt["route"] == route
    assert receipt["result"]["content"] == ("platform answer" if source == "platform" else "synthetic answer")
    assert calls == (["/api/v1/ai/resolve", "/api/v1/ai/chat", "/api/v1/ai/validate"] if source == "platform" else
                     ["/api/v1/ai/resolve", "/v1/chat/completions", "/api/v1/ai/validate"])
    asyncio.run(manager.validate(service_id="sample.ai", route=route, scope=SCOPE))
    assert calls[-1] == "/api/v1/ai/validate"


def test_missing_access_or_unavailable_platform_never_falls_back(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError(PROFILE["api_key"])
    manager = configured(tmp_path, handler, platform_base_url="http://localhost:19000")
    with pytest.raises(sdk.AIServiceError, match="ai_access_scope"):
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES))
    assert not calls
    with pytest.raises(sdk.AIServiceError, match="ai_platform_unavailable"):
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES, scope=SCOPE))
    assert len(calls) == 1


def test_in_flight_configuration_change_discards_answer(tmp_path):
    def handler(request):
        manager.put_config("sample.ai", expected_revision=1, profile={"model": "new-model"})
        return httpx.Response(200, json=provider_result())
    manager = configured(tmp_path, handler)
    with pytest.raises(sdk.AIServiceError, match="ai_config_revision_changed"):
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES))


@pytest.mark.parametrize("rejection", ["binding", "grant"])
def test_post_call_revision_or_authorization_change_denies_result(tmp_path, rejection):
    route = dict(service_id="sample.ai", source="module", binding_revision=3, module_config_revision=1, platform_config_digest=None)
    def handler(request):
        if request.url.path.endswith("/resolve"):
            return httpx.Response(200, json=route)
        if request.url.path.endswith("/validate"):
            return httpx.Response(403, json={"error": {"message": PROFILE["api_key"]}}) if rejection == "grant" else httpx.Response(200, json={**route, "binding_revision": 4})
        return httpx.Response(200, json=provider_result())
    manager = configured(tmp_path, handler, platform_base_url="http://localhost:19000")
    with pytest.raises(sdk.AIServiceError, match="ai_platform_rejected" if rejection == "grant" else "ai_binding_revision_changed") as error:
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES, scope=SCOPE))
    assert PROFILE["api_key"] not in str(error.value)


def test_route_pins_module_revision_before_network(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=dict(service_id="sample.ai", source="module", binding_revision=1, module_config_revision=2, platform_config_digest=None))
    manager = configured(tmp_path, handler, platform_base_url="http://localhost:19000")
    with pytest.raises(sdk.AIServiceError, match="ai_config_revision_changed"):
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES, scope=SCOPE))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_provider_error_is_safe_and_never_retried(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": PROFILE["api_key"]}})
    manager = configured(tmp_path, handler)
    with pytest.raises(sdk.AIServiceError) as error:
        asyncio.run(manager.chat(service_id="sample.ai", messages=MESSAGES))
    assert PROFILE["api_key"] not in str(error.value)
    assert len(calls) == 1


def test_control_requires_config_revision_for_real_provider_test(tmp_path):
    manager = configured(tmp_path, lambda _: httpx.Response(200, json=provider_result()))
    assert asyncio.run(manager.control("sample.ai", "POST", "test", {"expected_revision": 1}))["ok"] is True
    with pytest.raises(sdk.AIServiceError, match="ai_configuration"):
        asyncio.run(manager.control("sample.ai", "POST", "test", {}))
    with pytest.raises(sdk.AIServiceError, match="ai_control_not_found"):
        asyncio.run(manager.control("sample.ai", "POST", "config", {}))


@pytest.mark.parametrize("change", ["digest", "grant"])
def test_final_commit_guard_rechecks_platform_digest_and_authorization(tmp_path, change):
    changed, committed = False, False
    route = dict(service_id="sample.ai", source="platform", binding_revision=1, module_config_revision=None, platform_config_digest="a" * 64)
    def handler(request):
        if request.url.path.endswith("/chat"):
            assert json.loads(request.content)["platform_config_digest"] == route["platform_config_digest"]
            return httpx.Response(200, json={**route, "result": dict(content="answer", model="platform-model", tool_calls=[])})
        if changed and change == "grant":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={**route, "platform_config_digest": "b" * 64} if changed else route)
    manager = configured(tmp_path, handler, platform_base_url="http://localhost:19000")
    receipt = asyncio.run(manager.chat_with_receipt(service_id="sample.ai", messages=MESSAGES, scope=SCOPE))
    changed = True
    async def commit():
        nonlocal committed
        await manager.validate(service_id="sample.ai", scope=SCOPE, route=receipt["route"])
        committed = True
    with pytest.raises(sdk.AIServiceError, match="ai_platform_rejected" if change == "grant" else "ai_binding_revision_changed"):
        asyncio.run(commit())
    assert committed is False


def test_text_bounds_reject_before_outbound_request(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider_result())
    manager = configured(tmp_path, handler)
    for messages in [MESSAGES * 65, [{"role": "user", "content": "x" * 16001}], [{"role": "user", "content": "x", "tools": []}]]:
        with pytest.raises(sdk.AIServiceError, match="ai_request"):
            asyncio.run(manager.chat(service_id="sample.ai", messages=messages))
    assert not calls


def test_ai_registration_requires_feature_and_real_capability_binding(tmp_path):
    registration_spec = importlib.util.spec_from_file_location("ai_test_registration", Path(__file__).parents[1] / "registration.py")
    registration = importlib.util.module_from_spec(registration_spec)
    registration_spec.loader.exec_module(registration)
    schema = {"type": "object"}
    manifest = dict(protocol_version="1.2", module=dict(id="sample", version="1.0.0"), capabilities=[dict(id="sample.analyze", input_schema=schema, output_schema=schema)])
    document = dict(registration_version="1.0", module_id="sample", module_version="1.0.0", workflow_operations=[], permissions=dict(mode="platform_access"), ai_services=SERVICES)
    manager = configured(tmp_path, platform_base_url="http://localhost:19000")
    registrar = registration.Registration(manifest, document, {"sample.analyze": lambda: None}, ai_manager=manager)
    offer = dict(registration_version="1.0", nonce="a" * 32, contract_digest="b" * 64, manifest=manifest, registration=document,
                 platform_features=["invoke-1.1", "shared-hosts-1.0", "registration-1.0", "access-1.0"])
    with pytest.raises(registration.RegistrationError):
        registrar.confirm(offer)
    offer["platform_features"].append("ai-services-1.0")
    assert registrar.confirm(offer)["accepted"] is True
    with pytest.raises(registration.RegistrationError):
        registration.Registration(manifest, {**document, "ai_services": [{**SERVICES[0], "capability_ids": ["sample.missing"]}]}, {"sample.analyze": lambda: None})
