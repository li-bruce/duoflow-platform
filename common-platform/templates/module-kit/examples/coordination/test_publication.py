import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("publication_demo", Path(__file__).with_name("publication_demo.py"))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def client():
    return demo.coordination.CoordinationClient(base_url="http://127.0.0.1:19090/api/v1/coordination",
        token="synthetic-example-module-token-only", module_id=demo.SOURCE, consumer_id="source-worker",
        scopes=[demo.SCOPE], transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"acknowledged": True, "duplicate": False, "delivery_id": "decision-delivery"})))


def decision(request_id, kind="publication.approved"):
    return {"id": "decision-delivery", "subscription_id": "approval", "target_module": demo.SOURCE,
        "mode": "sync", "event": demo.make_event(kind, "pending", 2, "decision-original", request_id),
        "consumer_id": "source-worker", "lease_token": "synthetic-example-lease-token", "lease_until": 9999999999, "attempts": 1}


def test_publication_checks_original_revision_and_commits_state_outbox_inbox(tmp_path):
    conn = demo.open_database(tmp_path / "source.sqlite3")
    c = client()
    request_id = demo.request_publication(conn, c, expected_revision=1)
    assert demo.read_public(conn) is None
    assert c.consume(conn, decision(request_id), demo.publication_handler(c)) == {"applied": True, "acknowledged": True}
    assert demo.read_public(conn) == {"id": "batch-1", "state": "public", "revision": 3}
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_outbox").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_inbox").fetchone()[0] == 1
    assert c.consume(conn, decision(request_id), demo.publication_handler(c))["applied"] is False
    conn.close()


@pytest.mark.parametrize("changed", ["revision", "policy", "request"])
def test_stale_approval_and_changed_current_policy_do_not_publish(tmp_path, changed):
    conn = demo.open_database(tmp_path / "source.sqlite3")
    c = client()
    request_id = demo.request_publication(conn, c, expected_revision=1)
    with conn:
        if changed == "revision":
            conn.execute("UPDATE resources SET revision=3")
        elif changed == "policy":
            conn.execute("UPDATE publication_policy SET can_publish=0")
        else:
            conn.execute("UPDATE resources SET pending_request='another-request'")
    with pytest.raises(ValueError):
        c.consume(conn, decision(request_id), demo.publication_handler(c))
    assert demo.read_public(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM duoflow_coord_inbox").fetchone()[0] == 0
    conn.close()


def test_rejection_and_readers_only_refresh_index_or_notify(tmp_path):
    conn = demo.open_database(tmp_path / "source.sqlite3")
    c = client()
    request_id = demo.request_publication(conn, c, expected_revision=1)
    c.consume(conn, decision(request_id, "publication.rejected"), demo.publication_handler(c))
    assert demo.read_public(conn) is None
    assert conn.execute("SELECT state,revision FROM resources").fetchone() == ("private", 3)
    published = demo.make_event("resource.published", "public", 3)
    with conn:
        demo.reader_handler(conn, published, {"id": "notification-1", "mode": "notify"})
        demo.reader_handler(conn, published, {"id": "sync-1", "mode": "sync"})
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM index_hints").fetchone()[0] == 1
    # Receiving a publication reference does not mutate this module's resource.
    assert demo.read_public(conn) is None
    assert conn.execute("SELECT state,revision FROM resources").fetchone() == ("private", 3)
    conn.close()
