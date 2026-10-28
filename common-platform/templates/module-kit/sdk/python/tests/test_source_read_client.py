import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


spec = importlib.util.spec_from_file_location("portable_source_reads", Path(__file__).parents[1] / "source_reads.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
SOURCE = {"module_id": "example.source", "object_ref": {"object_type": "example.source.snapshot", "object_id": "S1"}, "revision": 7}


def run(handler, **changes):
    client = sdk.SourceReadClient("http://127.0.0.1:8031/api/v1/access/source-reads", transport=httpx.MockTransport(handler))
    return asyncio.run(client.read(ticket="synthetic-ticket", request_id="r1", operation="invoke",
        permission="consumer.use", facts={"project_id": "P"}, source=SOURCE, **changes))


def response(**changes):
    return {"source_read_version": "1.0", "source": SOURCE, "projection": {
        "object_ref": SOURCE["object_ref"], "revision": 7, "state": "published", "data": {"selected": "actual"}, "allowed_actions": []}, **changes}


def test_portable_reader_uses_fixed_callback_header_and_opaque_source():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response())
    result = run(handler, read_input={"fields": ["selected"]})
    assert result["projection"]["data"] == {"selected": "actual"}
    assert seen[0].headers["X-Platform-Access"] == "synthetic-ticket"
    assert "ticket" not in json.loads(seen[0].content)
    assert json.loads(seen[0].content)["read_input"] == {"fields": ["selected"]}


@pytest.mark.parametrize("url", ["https://host/wrong", "https://user:pass@host/api/v1/access/source-reads", "https://host/api/v1/access/source-reads?url=other"])
def test_portable_reader_rejects_credential_or_callback_override(url):
    with pytest.raises(sdk.SourceReadError, match="source_read_configuration"):
        sdk.SourceReadClient(url)


@pytest.mark.parametrize("payload,code", [
    (response(source={**SOURCE, "revision": 8}), "source_read_response"),
    (response(projection={"data": {"leak": "synthetic-ticket"}}), "source_read_credential"),
    ({"error": {"code": "source_revision_changed"}}, "source_revision_changed"),
])
def test_portable_reader_denies_mismatch_echo_and_public_error(payload, code):
    status = 409 if "error" in payload else 200
    with pytest.raises(sdk.SourceReadError, match=code):
        run(lambda request: httpx.Response(status, json=payload))


def test_portable_reader_bounds_response_and_refuses_redirect():
    with pytest.raises(sdk.SourceReadError, match="source_read_response_size"):
        run(lambda request: httpx.Response(200, content=b" " * 1100001))
    with pytest.raises(sdk.SourceReadError):
        run(lambda request: httpx.Response(302, headers={"location": "https://other.invalid"}))


@pytest.mark.parametrize("status", [403, 409, 422, 503])
def test_source_failure_preserves_safe_http_status_without_private_message(status):
    with pytest.raises(sdk.SourceReadError) as failure:
        run(lambda request: httpx.Response(status, json={"error": {
            "code": "source_fixture_error", "message": "private fixture detail"}}))
    assert failure.value.code == "source_fixture_error"
    assert failure.value.status == status
    assert str(failure.value) == "source_fixture_error"


def test_consumer_can_require_optional_feature_before_registration_acceptance():
    registration_spec = importlib.util.spec_from_file_location("portable_registration", Path(__file__).parents[1] / "registration.py")
    registration_sdk = importlib.util.module_from_spec(registration_spec)
    registration_spec.loader.exec_module(registration_sdk)
    manifest = {"protocol_version": "1.2", "module": {"id": "example.consumer", "version": "1.0.0"}, "capabilities": []}
    document = {"registration_version": "1.0", "module_id": "example.consumer", "module_version": "1.0.0", "workflow_operations": [], "permissions": {"mode": "capability_roles"}}
    features = ["source-reads-1.0"]
    registrar = registration_sdk.Registration(manifest, document, {}, required_platform_features=features)
    features.clear()
    offer = dict(registration_version="1.0", nonce="a" * 32, contract_digest="b" * 64,
                 manifest=manifest, registration=document,
                 platform_features=["invoke-1.1", "shared-hosts-1.0", "registration-1.0"])
    with pytest.raises(registration_sdk.RegistrationError):
        registrar.confirm(offer)
    offer["platform_features"].append("source-reads-1.0")
    assert registrar.confirm(offer)["accepted"]
