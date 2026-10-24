"""Portable client boundaries; no platform installation or application imports."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


SOURCE = Path(__file__).resolve().parents[1] / "remote_access.py"
spec = importlib.util.spec_from_file_location("kit_portable_remote_access", SOURCE)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
RemoteAccessClient = sdk.RemoteAccessClient
service_bearer_matches = sdk.service_bearer_matches
ENDPOINT = "http://127.0.0.1/api/v1/access/remote-decisions"
SUCCESS = {"allowed": True, "reason": "allowed", "policy_revision": 2}
REQUEST = dict(
    ticket="synthetic-ticket",
    request_id="req:one",
    operation="objects.read",
    permission="sample.item.read",
    facts={"project_id": "P", "owner_id": "alice"},
)


def run(client, **changes):
    return asyncio.run(client.decide(**{**REQUEST, **changes}))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/wrong",
        "ftp://example.test/api/v1/access/remote-decisions",
        "http:///api/v1/access/remote-decisions",
        "https://user:secret@example.test/api/v1/access/remote-decisions",
        "https://@example.test/api/v1/access/remote-decisions",
        ENDPOINT + "?q=1",
        ENDPOINT + "?",
        ENDPOINT + "#fragment",
        ENDPOINT + "#",
        ENDPOINT + "/",
        ENDPOINT + "\n",
        "http://127.0.0.1:0/api/v1/access/remote-decisions",
        "http://127.0.0.1:99999/api/v1/access/remote-decisions",
        "http://127.0.0.1/api/v1/access/%72emote-decisions",
        None,
    ],
)
def test_callback_rejects_nonfixed_or_credential_bearing_urls(url):
    with pytest.raises(ValueError, match="Invalid configured"):
        RemoteAccessClient(url)


@pytest.mark.parametrize("timeout", [0, -1, 10.1, True, None, "4", float("nan"), float("inf")])
def test_timeout_is_finite_bounded_and_not_boolean(timeout):
    with pytest.raises(ValueError, match="timeout"):
        RemoteAccessClient(ENDPOINT, timeout_seconds=timeout)


@pytest.mark.parametrize(
    "header,secret,expected",
    [
        ("Bearer synthetic-secret", "synthetic-secret", True),
        ("bEaReR synthetic-secret", "synthetic-secret", True),
        (None, "synthetic-secret", False),
        ("Bearer ", "", False),
        ("Bearer wrong", "synthetic-secret", False),
        ("Basic synthetic-secret", "synthetic-secret", False),
        ("Bearer  synthetic-secret", "synthetic-secret", False),
        ("Bearer synthetic-secret\n", "synthetic-secret", False),
        ("Bearer 合成", "合成", False),
        ("Bearer synthetic-secret", None, False),
    ],
)
def test_service_bearer_missing_or_malformed_never_defaults_open(header, secret, expected):
    assert service_bearer_matches(header, secret) is expected


def test_service_bearer_uses_constant_time_comparison(monkeypatch):
    calls = []
    original = sdk.hmac.compare_digest
    monkeypatch.setattr(
        sdk.hmac, "compare_digest", lambda a, b: (calls.append((a, b)), original(a, b))[1]
    )
    assert service_bearer_matches("Bearer synthetic-secret", "synthetic-secret")
    assert calls == [(b"synthetic-secret", b"synthetic-secret")]


def test_exact_body_ticket_header_and_environment_isolation(monkeypatch):
    observations = []
    client_options = []
    real_client = sdk.httpx.AsyncClient

    def construct(**kwargs):
        client_options.append(kwargs)
        return real_client(**kwargs)

    def callback(request):
        observations.append(request)
        return httpx.Response(200, json=SUCCESS)

    monkeypatch.setattr(sdk.httpx, "AsyncClient", construct)
    client = RemoteAccessClient(ENDPOINT, transport=httpx.MockTransport(callback))
    assert run(client) == SUCCESS
    request = observations[0]
    assert str(request.url) == ENDPOINT and request.method == "POST"
    assert request.headers["X-Platform-Access"] == REQUEST["ticket"]
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {k: v for k, v in REQUEST.items() if k != "ticket"}
    assert REQUEST["ticket"].encode() not in request.content
    assert client_options[0]["trust_env"] is False
    assert client_options[0]["follow_redirects"] is False
    assert client_options[0]["timeout"] == 4.0
    assert not any("ticket" in name or "facts" in name for name in vars(client))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"ticket": None}, "access_ticket_invalid"),
        ({"ticket": ""}, "access_ticket_invalid"),
        ({"ticket": "x" * (12 * 1024 + 1)}, "access_ticket_invalid"),
        ({"ticket": "x\r\nInjected: value"}, "access_ticket_invalid"),
        ({"ticket": "非ASCII"}, "access_ticket_invalid"),
        ({"request_id": "bad/id"}, "access_decision_contract"),
        ({"operation": "invoke\n"}, "access_decision_contract"),
        ({"permission": "a" * 161}, "access_decision_contract"),
        ({"facts": {"owner_id": None}}, "access_decision_contract"),
        ({"facts": {"owner_id": True}}, "access_decision_contract"),
        ({"facts": {"Owner": "alice"}}, "access_decision_contract"),
        ({"facts": {"teams": ["a", "a"]}}, "access_decision_contract"),
        ({"facts": {"teams": []}}, "access_decision_contract"),
        ({"facts": {"teams": [str(i) for i in range(21)]}}, "access_decision_contract"),
        ({"facts": {f"fact_{i}": "value" for i in range(33)}}, "access_decision_contract"),
        (
            {"facts": {f"fact_{i}": [str(j) + "合" * 150 for j in range(20)] for i in range(3)}},
            "access_decision_size",
        ),
    ],
)
def test_invalid_input_is_denied_before_transport(changes, reason):
    def callback(_request):
        pytest.fail("Invalid input must not reach the callback")

    result = run(RemoteAccessClient(ENDPOINT, transport=httpx.MockTransport(callback)), **changes)
    assert result == {"allowed": False, "reason": reason, "policy_revision": 0}


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {**SUCCESS, "actor": {"id": "private"}},
        {**SUCCESS, "allowed": 1},
        {**SUCCESS, "reason": "grant_scope_mismatch"},
        {**SUCCESS, "policy_revision": True},
        {**SUCCESS, "policy_revision": 0},
        {**SUCCESS, "policy_revision": -1},
        {**SUCCESS, "policy_revision": 1.5},
        {**SUCCESS, "reason": None},
        {"allowed": False, "reason": "private-provider-message", "policy_revision": 0},
        {"allowed": False, "reason": "allowed", "policy_revision": 1},
    ],
)
def test_invalid_response_fields_fail_closed(body):
    client = RemoteAccessClient(
        ENDPOINT, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))
    )
    assert run(client) == {
        "allowed": False,
        "reason": "access_decision_invalid",
        "policy_revision": 0,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON private content",
        b"\xff",
        b'{"allowed":true,"reason":"allowed","policy_revision":NaN}',
        b'{"allowed":false,"allowed":true,"reason":"allowed","policy_revision":2}',
    ],
)
def test_bad_json_duplicate_keys_and_nonfinite_never_escape(raw):
    result = run(
        RemoteAccessClient(
            ENDPOINT, transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=raw))
        )
    )
    assert result == {"allowed": False, "reason": "access_decision_invalid", "policy_revision": 0}


def test_normal_denial_is_preserved():
    denial = {"allowed": False, "reason": "grant_scope_mismatch", "policy_revision": 3}
    client = RemoteAccessClient(
        ENDPOINT, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=denial))
    )
    assert run(client) == denial


@pytest.mark.parametrize("status", [201, 302, 307, 401, 403, 429, 500])
def test_http_failure_or_redirect_never_follows_destination(status):
    calls = []

    def callback(request):
        calls.append(str(request.url))
        return httpx.Response(
            status,
            headers={"Location": "https://must-not-follow.invalid/secret"},
            content=b"private provider text",
        )

    result = run(RemoteAccessClient(ENDPOINT, transport=httpx.MockTransport(callback)))
    assert result == {
        "allowed": False,
        "reason": "access_decision_unavailable",
        "policy_revision": 0,
    }
    assert calls == [ENDPOINT]


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("private address"), httpx.ReadTimeout("private timeout")]
)
def test_network_failure_does_not_expose_exception(error):
    def callback(_request):
        raise error

    result = run(RemoteAccessClient(ENDPOINT, transport=httpx.MockTransport(callback)))
    assert result == {
        "allowed": False,
        "reason": "access_decision_unavailable",
        "policy_revision": 0,
    }


def test_total_deadline_also_applies_to_custom_transport():
    async def callback(_request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=SUCCESS)

    result = run(
        RemoteAccessClient(ENDPOINT, timeout_seconds=0.01, transport=httpx.MockTransport(callback))
    )
    assert result["reason"] == "access_decision_unavailable" and result["allowed"] is False


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.read, self.closed = chunks, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_stream_stops_at_byte_limit_and_closes_response():
    chunks = Chunks([b" " * 2048, b" " * 2048, b" ", b"private remainder"])
    client = RemoteAccessClient(
        ENDPOINT, transport=httpx.MockTransport(lambda _r: httpx.Response(200, stream=chunks))
    )
    assert run(client)["reason"] == "access_decision_response_size"
    assert chunks.read == 3 and chunks.closed


def test_exact_response_limit_is_accepted():
    body = json.dumps(SUCCESS).encode()
    chunks = Chunks([body, b" " * (4096 - len(body))])
    client = RemoteAccessClient(
        ENDPOINT, transport=httpx.MockTransport(lambda _r: httpx.Response(200, stream=chunks))
    )
    assert run(client) == SUCCESS and chunks.closed


def test_announced_oversized_response_is_not_read():
    chunks = Chunks([b"private body"])
    client = RemoteAccessClient(
        ENDPOINT,
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, headers={"Content-Length": "4097"}, stream=chunks)
        ),
    )
    assert run(client)["reason"] == "access_decision_response_size"
    assert chunks.read == 0 and chunks.closed


def test_compressed_response_is_rejected_without_reading():
    chunks = Chunks([b"private compressed bytes"])
    client = RemoteAccessClient(
        ENDPOINT,
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=chunks)
        ),
    )
    assert run(client)["reason"] == "access_decision_invalid"
    assert chunks.read == 0 and chunks.closed
