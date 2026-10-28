"""Runnable synthetic publication module and index/notification consumers.

The coordinator runs separately. See README.md for explicit demo config.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import uuid

_path = Path(__file__).parents[2] / "sdk" / "python" / "coordination.py"
_spec = importlib.util.spec_from_file_location("portable_coordination", _path)
coordination = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coordination)
SCOPE = {"organization_id": "demo-org", "project_id": "demo-project"}
SOURCE = "publication-demo"


def open_database(path):
    conn = sqlite3.connect(path)
    coordination.initialize(conn)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY,state TEXT NOT NULL,revision INTEGER NOT NULL,
            pending_request TEXT,organization_id TEXT NOT NULL,project_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publication_policy (id INTEGER PRIMARY KEY,can_publish INTEGER NOT NULL);
        INSERT OR IGNORE INTO publication_policy VALUES (1,1);
        INSERT OR IGNORE INTO resources VALUES ('batch-1','private',1,NULL,'demo-org','demo-project');
        CREATE TABLE IF NOT EXISTS index_hints (source_module TEXT,object_id TEXT,revision INTEGER,signal TEXT,
            PRIMARY KEY(source_module,object_id));
        CREATE TABLE IF NOT EXISTS notifications (delivery_id TEXT PRIMARY KEY,object_id TEXT,signal TEXT);
    """)
    return conn


def make_event(kind, state, revision, event_id=None, approval_ref=None):
    value = {"version": "1.0", "event_id": event_id or uuid.uuid4().hex, "module_id": SOURCE,
             "kind": kind, "machine_id": SOURCE + ".publication", "object_ref": {"object_type": SOURCE + ".batch", "object_id": "batch-1"},
             "state": state, "revision": revision, "scope": dict(SCOPE)}
    if approval_ref:
        value["approval_ref"] = approval_ref
    return value


def request_publication(conn, client, *, expected_revision):
    request_id = uuid.uuid4().hex
    # Existing module business command: compare revision before changing state.
    with conn:
        changed = conn.execute("""UPDATE resources SET state='pending',revision=revision+1,pending_request=?
            WHERE id='batch-1' AND state='private' AND revision=?""", (request_id, expected_revision)).rowcount
        if changed != 1:
            raise ValueError("申请前对象状态或修订已经变化")
        client.enqueue(conn, make_event("publication.requested", "pending", expected_revision+1, request_id))
    return request_id


def publication_handler(client):
    def handle(conn, event, _delivery):
        if event["kind"] not in {"publication.approved", "publication.rejected"}:
            raise ValueError("当前处理器只办理公开审批决定")
        row = conn.execute("SELECT state,revision,pending_request,organization_id,project_id FROM resources WHERE id=?",
                           (event["object_ref"]["object_id"],)).fetchone()
        if (event["module_id"] != SOURCE or event["machine_id"] != SOURCE + ".publication"
                or event["object_ref"]["object_type"] != SOURCE + ".batch" or event["state"] != "pending"
                or not row or row[:3] != ("pending", event["revision"], event["approval_ref"])
                or {"organization_id": row[3], "project_id": row[4]} != event["scope"]):
            raise ValueError("审批对应的申请已经变化，需要按当前修订重新办理")
        if event["kind"] == "publication.approved":
            # Current local business policy is checked inside this transaction.
            if conn.execute("SELECT can_publish FROM publication_policy WHERE id=1").fetchone()[0] != 1:
                raise ValueError("当前模块公开条件尚未满足")
            state, kind = "public", "resource.published"
        else:
            state, kind = "private", "state.changed"
        changed = conn.execute("""UPDATE resources SET state=?,revision=revision+1,pending_request=NULL
            WHERE id='batch-1' AND state='pending' AND revision=? AND pending_request=?""",
                               (state, event["revision"], event["approval_ref"])).rowcount
        if changed != 1:
            raise ValueError("对象已由另一办理人更新")
        # Formal state, new business event, and inbox receipt share one commit.
        client.enqueue(conn, make_event(kind, state, event["revision"]+1,
                                       approval_ref=event["approval_ref"] if state == "public" else None))
    return handle


def reader_handler(conn, event, delivery):
    if event["kind"] not in {"resource.published", "resource.withdrawn"}:
        raise ValueError("当前消费者只接收公开和撤下消息")
    if delivery["mode"] == "notify":
        conn.execute("INSERT INTO notifications VALUES (?,?,?)",
                     (delivery["id"], event["object_ref"]["object_id"], event["kind"]))
    else:
        conn.execute("""INSERT INTO index_hints VALUES (?,?,?,?) ON CONFLICT(source_module,object_id)
            DO UPDATE SET revision=excluded.revision,signal=excluded.signal
            WHERE excluded.revision>index_hints.revision""",
                     (event["module_id"], event["object_ref"]["object_id"], event["revision"], event["kind"]))
    # Only an index hint or notification is saved. A reader fetches actual data
    # through the source module's public/current-authorized API when needed.


def read_public(conn):
    row = conn.execute("SELECT id,state,revision FROM resources WHERE id='batch-1' AND state='public'").fetchone()
    return None if row is None else {"id": row[0], "state": row[1], "revision": row[2]}


def main():
    parser = argparse.ArgumentParser(description="合成公开审批及并行消费者示例")
    parser.add_argument("command", choices=["init", "request", "pump", "read", "summary"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--module-id", default=SOURCE)
    parser.add_argument("--expected-revision", type=int, default=1)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(args.database)
    try:
        if args.command == "init":
            result = {"initialized": True}
        elif args.command == "read":
            result = {"public_resource": read_public(conn)}
        elif args.command == "summary":
            result = {"resources": conn.execute("SELECT id,state,revision FROM resources").fetchall(),
                      "index_hints": conn.execute("SELECT * FROM index_hints").fetchall(),
                      "notifications": conn.execute("SELECT * FROM notifications").fetchall()}
        else:
            client = coordination.CoordinationClient(base_url=os.environ["DEMO_COORDINATION_URL"],
                token=os.environ["DEMO_MODULE_TOKEN"], module_id=args.module_id,
                consumer_id=args.module_id+"-worker", scopes=[SCOPE], accepted_sources=[SOURCE])
            if args.command == "request":
                result = {"request_id": request_publication(conn, client, expected_revision=args.expected_revision)}
            else:
                result = {"relay": client.relay(conn), "deliveries": []}
                handler = publication_handler(client) if args.module_id == SOURCE else reader_handler
                for item in client.pull():
                    result["deliveries"].append(client.consume(conn, item, handler))
                result["followup_relay"] = client.relay(conn)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
