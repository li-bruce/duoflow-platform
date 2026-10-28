import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

spec = importlib.util.spec_from_file_location("portable_coordination", Path(__file__).parents[1] / "coordination.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
SCOPE = {"organization_id": "example-org", "project_id": "example-project"}
TOKEN = "synthetic-module-token-for-coordination-only"


def event(number=1, object_id="sample"):
    return {"version": "1.0", "event_id": f"event-{number}", "module_id": "source", "kind": "state.changed",
            "machine_id": "resource", "object_ref": {"object_type": "resource", "object_id": object_id},
            "state": "pending", "revision": number, "scope": dict(SCOPE)}


def delivery():
    return {"id": "delivery-1", "subscription_id": "subscription-1", "target_module": "target", "mode": "notify",
            "event": event(), "consumer_id": "worker-1", "lease_token": "synthetic-lease-token-first", "lease_until": 9999999999, "attempts": 1}


def client(transport=None, **changes):
    return sdk.CoordinationClient(**{ "base_url": "http://127.0.0.1:19090/api/v1/coordination", "token": TOKEN,
        "module_id": "source", "consumer_id": "worker-1", "scopes": [SCOPE], "accepted_sources": ["source"],
        "transport": transport, **changes})


def database(path):
    conn = sqlite3.connect(path)
    sdk.initialize(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS business(id TEXT PRIMARY KEY, state TEXT NOT NULL, applications INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    return conn


def test_transaction_rollback_restart_retry_and_object_order(tmp_path):
    path = tmp_path / "module.sqlite3"
    conn = database(path)
    current = [100]
    c = client(httpx.MockTransport(lambda _: httpx.Response(503)), clock=lambda: current[0])
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("INSERT INTO business VALUES ('sample','pending',0)")
            c.enqueue(conn, event())
            raise RuntimeError("rollback")
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_outbox").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM business").fetchone()[0] == 0
    with conn:
        conn.execute("INSERT INTO business VALUES ('sample','pending',0)")
        sdk.enqueue(conn, event())
        c.enqueue(conn, event(2))
        c.enqueue(conn, event(3, "other-object"))
        assert c.enqueue(conn, event()) is False
        with pytest.raises(sdk.CoordinationError, match="coordination_event_conflict"):
            c.enqueue(conn, {**event(), "state": "changed"})
    assert c.relay(conn) == {"sent": 0, "failed": 2}
    assert c.relay(conn) == {"sent": 0, "failed": 0}
    conn.close()
    conn = database(path)
    received = []

    def accept(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert TOKEN.encode() not in request.content
        received.append(body["event_id"])
        return httpx.Response(200, json={"accepted": True, "duplicate": False, "event_id": body["event_id"]})

    c = client(httpx.MockTransport(accept), clock=lambda: current[0])
    current[0] += 2
    assert c.relay(conn) == {"sent": 2, "failed": 0}
    assert c.relay(conn) == {"sent": 1, "failed": 0}
    assert received == ["event-1", "event-3", "event-2"]
    assert conn.execute("SELECT SUM(sent) FROM duoflow_coord_outbox").fetchone()[0] == 3
    conn.close()


def test_inbox_handler_failure_ack_loss_restart_and_fingerprint(tmp_path):
    path = tmp_path / "module.sqlite3"
    conn = database(path)
    conn.execute("INSERT INTO business VALUES ('sample','pending',0)")
    conn.commit()
    acknowledgements = []

    def ack(request):
        assert not conn.in_transaction
        acknowledgements.append(json.loads(request.content))
        if len(acknowledgements) == 1:
            raise httpx.ReadTimeout("SENSITIVE BODY MUST NOT ESCAPE")
        return httpx.Response(200, json={"acknowledged": True, "duplicate": False, "delivery_id": "delivery-1"})

    c = client(httpx.MockTransport(ack), module_id="target")

    def fail(db, *_):
        db.execute("UPDATE business SET applications=applications+1")
        raise RuntimeError("business validation failed")

    with pytest.raises(RuntimeError):
        c.consume(conn, delivery(), fail)
    assert not acknowledgements
    assert conn.execute("SELECT applications FROM business").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_inbox").fetchone()[0] == 0

    def apply(db, *_):
        db.execute("UPDATE business SET applications=applications+1")

    assert c.consume(conn, delivery(), apply) == {"applied": True, "acknowledged": False, "error": "coordination_timeout"}
    conn.close()
    conn = database(path)
    repeated = {**delivery(), "lease_token": "synthetic-lease-token-second", "attempts": 2}
    assert c.consume(conn, repeated, apply) == {"applied": False, "acknowledged": True}
    assert acknowledgements[-1]["lease_token"] == repeated["lease_token"]
    assert conn.execute("SELECT applications FROM business").fetchone()[0] == 1
    with pytest.raises(sdk.CoordinationError, match="coordination_delivery_conflict"):
        c.consume(conn, {**repeated, "event": {**event(), "state": "changed"}}, apply)
    conn.close()


def test_delivery_policy_pull_and_receipt_rejections(tmp_path):
    item = delivery()
    c = client(httpx.MockTransport(lambda _: httpx.Response(200, json={"version": "1.0", "module_id": "target",
        "consumer_id": "worker-1", "deliveries": [item]})), module_id="target")
    assert c.pull() == [item]
    for patch in [{"target_module": "elsewhere"}, {"consumer_id": "elsewhere"}, {"lease_token": "short"},
                  {"event": {**event(), "scope": {**SCOPE, "project_id": "other"}}},
                  {"event": {**event(), "module_id": "forged"}}]:
        with pytest.raises(sdk.CoordinationError):
            c._delivery({**item, **patch})
    with pytest.raises(sdk.CoordinationError):
        c.pull(limit=21)
    with pytest.raises(sdk.CoordinationError):
        sdk.validate_event({**event(), "payload": "private"})
    conn = database(tmp_path / "isolated.sqlite3")
    with pytest.raises(sdk.CoordinationError, match="coordination_transaction"):
        sdk.enqueue(conn, event())
    conn.close()


def test_real_http_timeout_is_bounded_and_outbox_remains(tmp_path):
    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            time.sleep(.2)
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(b'{}')
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = database(tmp_path / "timeout.sqlite3")
    c = client(base_url=f"http://127.0.0.1:{server.server_port}/api/v1/coordination", timeout_seconds=.05)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            c.enqueue(conn, event())
        started = time.monotonic()
        assert c.relay(conn) == {"sent": 0, "failed": 1}
        assert time.monotonic()-started < 1
        assert conn.execute("SELECT sent,last_error FROM duoflow_coord_outbox").fetchone() == (0, "coordination_timeout")
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_configuration_rejects_remote_http_credentials_and_wildcards():
    for changes in [{"base_url": "http://remote.example/api/v1/coordination"}, {"token": "short"},
                    {"scopes": [{"organization_id": "x"}]}, {"accepted_sources": ["*"]}, {"timeout_seconds": True}]:
        with pytest.raises(sdk.CoordinationError, match="coordination_configuration"):
            client(**changes)


def test_accepted_event_lost_response_and_async_handler_is_rejected(tmp_path):
    path = tmp_path / "lost-response.sqlite3"
    conn = database(path)
    now, calls = [100], []

    def lost_response(request):
        body = json.loads(request.content)
        calls.append(body["event_id"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("response lost after the center committed")
        return httpx.Response(200, json={"accepted": True, "duplicate": True, "event_id": body["event_id"]})

    c = client(httpx.MockTransport(lost_response), clock=lambda: now[0])
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        c.enqueue(conn, event())
    assert c.relay(conn) == {"sent": 0, "failed": 1}
    conn.close()
    conn = database(path)
    now[0] += 2
    assert c.relay(conn) == {"sent": 1, "failed": 0}
    assert calls == ["event-1", "event-1"]

    async def asynchronous_handler(*_):
        raise AssertionError("must never execute")

    target = client(httpx.MockTransport(lost_response), module_id="target")
    with pytest.raises(sdk.CoordinationError, match="coordination_handler_transaction"):
        target.consume(conn, delivery(), asynchronous_handler)
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_inbox").fetchone()[0] == 0
    conn.close()
