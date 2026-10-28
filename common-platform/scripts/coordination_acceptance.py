"""Real HTTP acceptance with one center and four isolated synthetic modules.

Run with the project virtualenv. Child processes receive only fresh private
configs under --work-directory; teardown signals their exact Popen handles.
This checks the framework protocol/SDK, not the existing four business services.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx
from fastapi import Request

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREFIX = "/api/v1/coordination"
NAMES = ("intake", "mining", "research", "governance")
SOURCE = "synthetic.intake"
SCOPE = {"organization_id": "synthetic-org", "project_id": "synthetic-project"}


def load_sdk():
    path = ROOT / "templates/module-kit/sdk/python/coordination.py"
    spec = importlib.util.spec_from_file_location("process_acceptance_sdk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker_app(config):
    """Small synthetic business HTTP server using the shipped SDK unchanged."""
    from fastapi import FastAPI, HTTPException

    sdk = load_sdk()
    identifier = config["module_id"]
    database = Path(config["database"])
    database.parent.mkdir(parents=True, exist_ok=True)
    client = sdk.CoordinationClient(base_url=config["center"] + PREFIX,
        token=config["token"], module_id=identifier, consumer_id=identifier + ".worker",
        scopes=[SCOPE], accepted_sources=[SOURCE])
    with closing(sqlite3.connect(database)) as db:
        sdk.initialize(db)
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY,state TEXT NOT NULL,
                revision INTEGER NOT NULL,pending_request TEXT,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publication_policy(id INTEGER PRIMARY KEY,allowed INTEGER NOT NULL);
            INSERT OR IGNORE INTO publication_policy VALUES(1,1);
            CREATE TABLE IF NOT EXISTS effects(delivery_id TEXT PRIMARY KEY,source_module TEXT NOT NULL,
                object_id TEXT NOT NULL,kind TEXT NOT NULL,revision INTEGER NOT NULL,mode TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS index_hints(source_module TEXT,object_id TEXT,state TEXT,revision INTEGER,
                PRIMARY KEY(source_module,object_id));
            CREATE TABLE IF NOT EXISTS work_log(id TEXT PRIMARY KEY,operation TEXT,started REAL,finished REAL);
        """)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    last_receipt = []

    def control(request):
        if request.headers.get("Authorization") != "Bearer " + config["token"]:
            raise HTTPException(403, "synthetic module control permission required")

    def read_permission(request):
        if (request.headers.get("Authorization") != "Bearer " + config["reader_token"]
                or request.headers.get("X-Project") != SCOPE["project_id"]):
            raise HTTPException(403, "synthetic source data permission required")

    def event(object_id, state, revision, kind="state.changed", event_id=None, approval_ref=None):
        value = {"version": "1.0", "event_id": event_id or uuid.uuid4().hex,
            "module_id": identifier, "kind": kind, "machine_id": identifier + ".lifecycle",
            "object_ref": {"object_type": identifier + ".record", "object_id": object_id},
            "state": state, "revision": revision, "scope": dict(SCOPE)}
        if approval_ref:
            value["approval_ref"] = approval_ref
        return value

    def handle(db, message, delivery):
        object_id = message["object_ref"]["object_id"]
        if message["kind"] in {"publication.approved", "publication.rejected"}:
            row = db.execute("SELECT state,revision,pending_request FROM resources WHERE id=?", (object_id,)).fetchone()
            if (identifier != SOURCE or message["module_id"] != identifier
                    or message["machine_id"] != identifier + ".lifecycle"
                    or message["object_ref"]["object_type"] != identifier + ".record"
                    or message["state"] != "pending" or message["scope"] != SCOPE
                    or row != ("pending", message["revision"], message["approval_ref"])):
                raise ValueError("approval no longer matches the local business object")
            if db.execute("SELECT allowed FROM publication_policy WHERE id=1").fetchone()[0] != 1:
                raise ValueError("current local publication policy denied")
            state = "public" if message["kind"] == "publication.approved" else "draft"
            updated = db.execute("UPDATE resources SET state=?,revision=revision+1,pending_request=NULL "
                "WHERE id=? AND state='pending' AND revision=? AND pending_request=?",
                (state, object_id, message["revision"], message["approval_ref"])).rowcount
            if updated != 1:
                raise ValueError("local business revision changed")
            client.enqueue(db, event(object_id, state, message["revision"] + 1,
                "resource.published" if state == "public" else "state.changed",
                approval_ref=message["approval_ref"] if state == "public" else None))
        elif delivery["mode"] == "sync":
            db.execute("INSERT INTO index_hints VALUES(?,?,?,?) ON CONFLICT(source_module,object_id) "
                "DO UPDATE SET state=excluded.state,revision=excluded.revision WHERE excluded.revision>index_hints.revision",
                (message["module_id"], object_id, message["state"], message["revision"]))
        db.execute("INSERT INTO effects VALUES(?,?,?,?,?,?)", (delivery["id"], message["module_id"],
            object_id, message["kind"], message["revision"], delivery["mode"]))

    @app.get("/health")
    def health():
        return {"service": "synthetic-coordination-module", "module_id": identifier, "pid": os.getpid()}

    @app.post("/work")
    async def work(request: Request):
        control(request)
        command = await request.json()
        object_id = command["object_id"]
        started = time.time()
        await asyncio.sleep(command.get("delay_seconds", 0))
        with closing(sqlite3.connect(database, timeout=5)) as db:
            with db:
                row = db.execute("SELECT state,revision FROM resources WHERE id=?", (object_id,)).fetchone()
                if row and row[0] != "draft":
                    raise HTTPException(409, "work requires current draft")
                revision = row[1] + 1 if row else 1
                db.execute("INSERT INTO resources VALUES(?,'draft',?,NULL,?) ON CONFLICT(id) "
                    "DO UPDATE SET revision=excluded.revision", (object_id, revision, "synthetic-body-" + identifier))
                changed = event(object_id, "draft", revision)
                client.enqueue(db, changed)
                finished = time.time()
                db.execute("INSERT INTO work_log VALUES(?,?,?,?)", (uuid.uuid4().hex,
                    "query" if identifier.endswith(".mining") else "business-work", started, finished))
            relay = client.relay(db)
            query = db.execute("SELECT count(*) FROM index_hints WHERE state='public'").fetchone()[0]
        return {"module_id": identifier, "pid": os.getpid(), "state": "draft", "revision": revision,
            "started": started, "finished": finished, "query_visible_count": query, "event": changed, "relay": relay}

    @app.post("/request-publication/{object_id}")
    async def request_publication(object_id: str, request: Request):
        control(request)
        command = await request.json()
        changed = event(object_id, "pending", command["expected_revision"] + 1, "publication.requested")
        with closing(sqlite3.connect(database, timeout=5)) as db:
            with db:
                count = db.execute("UPDATE resources SET state='pending',revision=revision+1,pending_request=? "
                    "WHERE id=? AND state='draft' AND revision=?",
                    (changed["event_id"], object_id, command["expected_revision"])).rowcount
                if count != 1:
                    raise HTTPException(409, "request revision changed")
                client.enqueue(db, changed)
            relay = client.relay(db)
        return {"request_event": changed, "relay": relay}

    @app.post("/withdraw/{object_id}")
    def withdraw(object_id: str, request: Request):
        control(request)
        with closing(sqlite3.connect(database, timeout=5)) as db:
            with db:
                row = db.execute("SELECT state,revision FROM resources WHERE id=?", (object_id,)).fetchone()
                if not row or row[0] != "public":
                    raise HTTPException(409, "only current public objects can be withdrawn")
                db.execute("UPDATE resources SET state='withdrawn',revision=revision+1 WHERE id=?", (object_id,))
                changed = event(object_id, "withdrawn", row[1] + 1, "resource.withdrawn")
                client.enqueue(db, changed)
            relay = client.relay(db)
        return {"event": changed, "relay": relay}

    @app.post("/pump")
    async def pump(request: Request):
        control(request)
        command = await request.json()
        results = []
        original = client._request

        def faulty(method, suffix, payload=None):
            if suffix.endswith("/ack") and command.get("drop_ack"):
                if command["drop_ack"] == "after":
                    original(method, suffix, payload)
                raise sdk.CoordinationError("synthetic_ack_response_lost")
            return original(method, suffix, payload)

        with closing(sqlite3.connect(database, timeout=5)) as db:
            first = client.relay(db)
            items = client.pull()
            client._request = faulty
            try:
                for item in items:
                    result = client.consume(db, item, handle)
                    last_receipt[:] = [item]
                    results.append({**result, "delivery_id": item["id"], "mode": item["mode"],
                        "kind": item["event"]["kind"], "object_id": item["event"]["object_ref"]["object_id"],
                        "revision": item["event"]["revision"], "attempts": item["attempts"]})
            finally:
                client._request = original
            followup = client.relay(db)
        return {"module_id": identifier, "results": results, "relay": first, "followup_relay": followup}

    @app.post("/repeat-ack")
    def repeat_ack(request: Request):
        control(request)
        if not last_receipt:
            raise HTTPException(409, "no receipt")
        item = last_receipt[0]
        return client._request("POST", "/deliveries/" + item["id"] + "/ack",
            {"consumer_id": client.consumer_id, "lease_token": item["lease_token"]})

    @app.get("/snapshot")
    def snapshot(request: Request):
        control(request)
        with closing(sqlite3.connect(database, timeout=5)) as db:
            return {"module_id": identifier, "pid": os.getpid(),
                "resources": [dict(zip(("id", "state", "revision"), row)) for row in db.execute("SELECT id,state,revision FROM resources ORDER BY id")],
                "effects": [dict(zip(("delivery_id", "source_module", "object_id", "kind", "revision", "mode"), row))
                    for row in db.execute("SELECT * FROM effects ORDER BY rowid")],
                "index_hints": [dict(zip(("source_module", "object_id", "state", "revision"), row))
                    for row in db.execute("SELECT * FROM index_hints ORDER BY object_id")],
                "inbox_count": db.execute("SELECT count(*) FROM duoflow_coord_inbox").fetchone()[0],
                "outbox_pending": db.execute("SELECT count(*) FROM duoflow_coord_outbox WHERE sent=0").fetchone()[0]}

    @app.get("/query")
    def query(request: Request):
        read_permission(request)
        with closing(sqlite3.connect(database)) as db:
            return {"public_index_count": db.execute("SELECT count(*) FROM index_hints WHERE state='public'").fetchone()[0]}

    @app.get("/objects/{object_id}")
    def read(object_id: str, request: Request):
        read_permission(request)
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT state,revision,body FROM resources WHERE id=? AND state='public'", (object_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no currently public object")
        return {"object_id": object_id, "state": row[0], "revision": row[1], "body": row[2]}

    return app


def serve(role, config_path):
    import uvicorn
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if role == "center":
        from coordination_service.main import create_app
        app = create_app(config["directory"], control_token=config["control_token"])
    else:
        app = worker_app(config)
    uvicorn.run(app, host="127.0.0.1", port=config["port"], log_level="warning", access_log=False)


class ProcessGroup:
    def __init__(self, directory):
        self.directory, self.items = directory, []

    def start(self, name, role, config):
        config_path = self.directory / (name + ".private.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        log = (self.directory / (name + ".log")).open("ab")
        env = {**os.environ, "PLATFORM_DATA_DIR": str(self.directory / "unused-platform-data"), "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", role,
            "--config", str(config_path)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        log.close()
        self.items.append((name, process))
        url = "http://127.0.0.1:" + str(config["port"])
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} process exited; see its private work log")
                try:
                    result = client.get(url + "/health")
                    if result.status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        raise RuntimeError(name + " did not become healthy")

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def close(self):
        for _, process in reversed(self.items):
            self.stop(process)
        return [{"name": name, "pid": process.pid, "exited": process.poll() is not None} for name, process in self.items]


def free_port():
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_acceptance(work_directory, report_path):
    from coordination_service.protocol import LEASE_SECONDS

    started = time.monotonic()
    directory = Path(work_directory).resolve()
    directory.relative_to(WORKSPACE)
    directory.mkdir(parents=True, exist_ok=False)
    report_path = Path(report_path).resolve()
    report_path.relative_to(WORKSPACE)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    group = ProcessGroup(directory)
    report = {"kind": "isolated-synthetic-framework-process-acceptance", "version": "1.0",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "独立中心与四个隔离合成模块的协议及SDK验收", "existing_business_modules_adapted": False,
        "fault_injections": {"ack_response_loss": "真实HTTP确认已到中心，再在客户端抛出响应丢失错误",
            "ack_request_loss": "模块事务提交后阻止一次ACK发送，重启进程并等待真实租约到期"},
        "checks": [], "processes": {}, "timings": {}}

    def check(name, condition, observed):
        report["checks"].append({"name": name, "passed": bool(condition), "observed": observed})
        if not condition:
            raise AssertionError(name)

    def call(url, token, method, path, payload=None, expected=200, **headers):
        with httpx.Client(timeout=8, trust_env=False) as client:
            response = client.request(method, url + path, headers={"Authorization": "Bearer " + token, **headers}, json=payload)
        if response.status_code != expected:
            raise AssertionError(f"HTTP {method} {path}: expected {expected}, received {response.status_code}")
        return response.json()

    center_config = {"port": free_port(), "directory": str(directory / "center-db"), "control_token": secrets.token_urlsafe(36)}
    center_url = "http://127.0.0.1:" + str(center_config["port"])
    configs, urls, processes = {}, {}, {}
    for name in NAMES:
        configs[name] = {"module_id": "synthetic." + name, "port": free_port(), "database": str(directory / name / "business.sqlite3"),
            "center": center_url, "token": secrets.token_urlsafe(36), "reader_token": secrets.token_urlsafe(36)}
        urls[name] = "http://127.0.0.1:" + str(configs[name]["port"])

    def module(name, method, path, payload=None, **kwargs):
        return call(urls[name], configs[name]["token"], method, path, payload, **kwargs)

    def center(method, path, payload=None, token=None, expected=200):
        return call(center_url, token or center_config["control_token"], method, PREFIX + path, payload, expected)

    def snapshot(name):
        return module(name, "GET", "/snapshot")

    def query(name):
        return call(urls[name], configs[name]["reader_token"], "GET", "/query", **{"X-Project": SCOPE["project_id"]})

    def count_kind(value, kind):
        return sum(item["kind"] == kind for item in value["effects"])

    try:
        center_process = group.start("center", "center", center_config)
        for name in NAMES:
            processes[name] = group.start(name, "module", configs[name])
        report["processes"] = {"center": {"pid": center_process.pid, "port": center_config["port"]},
            **{name: {"pid": processes[name].pid, "port": configs[name]["port"]} for name in NAMES}}
        check("five_separate_processes_and_databases", len({p.pid for _, p in group.items}) == 5,
            {"process_count": len(group.items), "database_count": 5})
        modules, subscriptions = [], []
        for name in NAMES:
            identifier = configs[name]["module_id"]
            mode = "notify" if name == "research" else "sync"
            accepts = [] if name == "intake" else [{"source_module": SOURCE, "kind": kind, "mode": mode}
                for kind in ("resource.published", "resource.withdrawn")]
            if name == "governance":
                accepts.append({"source_module": SOURCE, "kind": "state.changed", "mode": "notify"})
            modules.append({"id": identifier, "token": configs[name]["token"], "enabled": True,
                "machines": [{"id": identifier + ".lifecycle", "object_types": [identifier + ".record"],
                    "states": ["draft", "pending", "public", "withdrawn"], "public_states": ["public"], "approval_required": name == "intake"}],
                "emits": ["state.changed", "publication.requested", "resource.published", "resource.withdrawn"], "accepts": accepts})
            if name != "intake":
                subscriptions.extend({"id": name + "." + kind, "source_module": SOURCE, "target_module": identifier,
                    "kind": kind, "scope": dict(SCOPE), "mode": mode} for kind in ("resource.published", "resource.withdrawn"))
        subscriptions.append({"id": "governance.states", "source_module": SOURCE, "target_module": "synthetic.governance",
            "kind": "state.changed", "scope": dict(SCOPE), "mode": "notify"})
        center("PUT", "/config", {"expected_revision": 0, "modules": modules, "subscriptions": subscriptions})

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(module, name, "POST", "/work", {"object_id": "batch-A" if name == "intake" else "work-1", "delay_seconds": .5}) for name in NAMES}
            parallel = {name: future.result() for name, future in futures.items()}
        overlap = min(item["finished"] for item in parallel.values()) - max(item["started"] for item in parallel.values())
        check("four_modules_work_concurrently", overlap > .25 and all(item["relay"]["sent"] == 1 for item in parallel.values()),
            {"overlap_seconds": round(overlap, 4), "operations": {name: {key: item[key] for key in ("started", "finished", "state", "revision", "query_visible_count")} for name, item in parallel.items()}})
        module("governance", "POST", "/pump", {})
        request = module("intake", "POST", "/request-publication/batch-A", {"expected_revision": 1})["request_event"]
        summary = center("GET", "/summary")
        check("publication_waits_for_administrator", summary["counts"]["approvals_pending"] == 1
            and request["revision"] == 2 and summary["approvals"][0]["status"] == "pending",
            {"request_revision": request["revision"], "approval_status": summary["approvals"][0]["status"]})
        call(urls["intake"], configs["intake"]["reader_token"], "GET", "/objects/batch-A", expected=404, **{"X-Project": SCOPE["project_id"]})
        module("mining", "POST", "/pump", {})
        module("research", "POST", "/pump", {})
        check("pending_resource_absent_from_public_read_and_mining", query("mining")["public_index_count"] == 0
            and not snapshot("research")["effects"], {"public_read_status": 404, "mining_index_count": 0, "research_publication_notifications": 0})
        with ThreadPoolExecutor(max_workers=3) as pool:
            other = list(pool.map(lambda name: module(name, "POST", "/work", {"object_id": "work-1", "delay_seconds": .2}), NAMES[1:]))
        check("other_businesses_continue_during_approval", all(item["revision"] == 2 for item in other)
            and center("GET", "/summary")["counts"]["approvals_pending"] == 1,
            {"independent_updates": len(other), "revisions": [item["revision"] for item in other], "approval_still_pending": True})
        decision = {"expected_revision": 1, "decision": "approved", "actor_id": "synthetic-administrator"}
        center("POST", "/approvals/" + request["event_id"] + "/decide", decision, token=configs["intake"]["token"], expected=401)
        check("module_credential_cannot_approve", True, {"http_status": 401})
        group.stop(processes["research"])
        approved = center("POST", "/approvals/" + request["event_id"] + "/decide", decision)
        check("decision_bound_to_original_revision", approved["approval"]["object_revision"] == request["revision"]
            and approved["approval"]["status"] == "approved", {"object_revision": approved["approval"]["object_revision"], "decision_revision": approved["approval"]["revision"]})
        source_pump = module("intake", "POST", "/pump", {})
        source = snapshot("intake")
        check("source_applies_approval_and_transactional_outbox", source["resources"][0]["state"] == "public"
            and source["resources"][0]["revision"] == 3 and source_pump["followup_relay"]["sent"] == 1,
            {"formal_state": source["resources"][0]["state"], "revision": 3, "published_events_relayed": source_pump["followup_relay"]["sent"]})
        published_summary = center("GET", "/summary")
        check("public_event_fans_out_to_three_consumers", published_summary["counts"]["deliveries_pending"] == 3,
            {"pending_deliveries": published_summary["counts"]["deliveries_pending"], "modes": {"mining": "sync", "research": "notify", "governance": "sync"}})
        before_duplicates = published_summary["counts"]["events"]
        duplicate = center("POST", "/events", request, token=configs["intake"]["token"])
        check("repeated_event_is_idempotent", duplicate["duplicate"] is True
            and center("GET", "/summary")["counts"]["events"] == before_duplicates,
            {"duplicate": duplicate["duplicate"], "event_count_unchanged": True})
        mining_ack_loss = module("mining", "POST", "/pump", {"drop_ack": "after"})
        repeated_ack = module("mining", "POST", "/repeat-ack")
        check("ack_response_loss_retries_same_receipt", mining_ack_loss["results"][0]["applied"]
            and not mining_ack_loss["results"][0]["acknowledged"] and repeated_ack["duplicate"]
            and count_kind(snapshot("mining"), "resource.published") == 1,
            {"application_count": 1, "duplicate_ack": repeated_ack["duplicate"], "acknowledged": repeated_ack["acknowledged"]})
        governance_ack_loss = module("governance", "POST", "/pump", {"drop_ack": "before"})
        lease_started = time.monotonic()
        old_delivery = governance_ack_loss["results"][0]["delivery_id"]
        check("ack_request_loss_commits_business_before_receipt", governance_ack_loss["results"][0]["applied"]
            and not governance_ack_loss["results"][0]["acknowledged"]
            and count_kind(snapshot("governance"), "resource.published") == 1,
            {"application_count": 1, "acknowledged": False, "lease_seconds": LEASE_SECONDS})

        call(urls["intake"], configs["mining"]["token"], "GET", "/objects/batch-A", expected=403, **{"X-Project": SCOPE["project_id"]})
        call(urls["intake"], configs["intake"]["reader_token"], "GET", "/objects/batch-A", expected=403, **{"X-Project": "other-project"})
        authorized = call(urls["intake"], configs["intake"]["reader_token"], "GET", "/objects/batch-A", **{"X-Project": SCOPE["project_id"]})
        check("metadata_delivery_preserves_source_data_authorization", authorized["state"] == "public"
            and "synthetic-body-" not in json.dumps(published_summary),
            {"coordination_credential_read_status": 403, "wrong_project_read_status": 403, "authorized_read_status": 200, "center_contains_body": False})

        old_center = center_process.pid
        group.stop(center_process)
        center_process = group.start("center-restarted", "center", center_config)
        check("center_restart_preserves_state_and_pending_deliveries", center_process.pid != old_center
            and center("GET", "/summary")["counts"]["events"] == before_duplicates,
            {"old_pid": old_center, "new_pid": center_process.pid, "events": before_duplicates})
        old_governance = processes["governance"].pid
        group.stop(processes["governance"])
        processes["governance"] = group.start("governance-restarted", "module", configs["governance"])
        check("consumer_restart_preserves_committed_inbox", processes["governance"].pid != old_governance
            and count_kind(snapshot("governance"), "resource.published") == 1,
            {"old_pid": old_governance, "new_pid": processes["governance"].pid, "publication_application_count": 1})
        processes["research"] = group.start("research-restarted", "module", configs["research"])
        research_catchup = module("research", "POST", "/pump", {})
        check("offline_consumer_catches_up_after_restart", len(research_catchup["results"]) == 1
            and research_catchup["results"][0]["mode"] == "notify"
            and count_kind(snapshot("research"), "resource.published") == 1,
            {"notify_count": 1, "previous_local_business_revision": snapshot("research")["resources"][0]["revision"]})

        for object_id in ("order-A", "order-A", "order-B"):
            module("intake", "POST", "/work", {"object_id": object_id})
        first_order = module("governance", "POST", "/pump", {})["results"]
        second_order = module("governance", "POST", "/pump", {})["results"]
        first_values = {(item["object_id"], item["revision"]) for item in first_order}
        check("one_object_ordered_other_objects_progress", first_values == {("order-A", 1), ("order-B", 1)}
            and {(item["object_id"], item["revision"]) for item in second_order} == {("order-A", 2)},
            {"first_pull": [[item["object_id"], item["revision"]] for item in first_order],
             "second_pull": [[item["object_id"], item["revision"]] for item in second_order], "publication_lease_still_pending": True})
        remaining = lease_started + LEASE_SECONDS + .3 - time.monotonic()
        while remaining > 0:
            time.sleep(min(1, remaining))
            remaining = lease_started + LEASE_SECONDS + .3 - time.monotonic()
        redelivery = module("governance", "POST", "/pump", {})["results"]
        check("lease_expiry_redelivery_does_not_reapply_after_restart", len(redelivery) == 1
            and redelivery[0]["delivery_id"] == old_delivery and redelivery[0]["attempts"] == 2
            and not redelivery[0]["applied"] and redelivery[0]["acknowledged"]
            and count_kind(snapshot("governance"), "resource.published") == 1,
            {"attempts": redelivery[0]["attempts"] if redelivery else None, "reapplied": redelivery[0]["applied"] if redelivery else None,
             "application_count": count_kind(snapshot("governance"), "resource.published"), "waited_seconds": round(time.monotonic() - lease_started, 3)})
        module("intake", "POST", "/withdraw/batch-A")
        for name in NAMES[1:]:
            module(name, "POST", "/pump", {})
        check("withdrawal_updates_each_parallel_consumer", query("mining")["public_index_count"] == 0
            and all(count_kind(snapshot(name), "resource.withdrawn") == 1 for name in NAMES[1:]),
            {"mining_public_index_count": 0, "withdrawal_consumers": 3})
        report["final_summary"] = center("GET", "/summary")
        report["final_module_snapshots"] = {name: snapshot(name) for name in NAMES}
        report["timings"]["real_lease_seconds"] = LEASE_SECONDS
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["cleanup"] = group.close()
        report["timings"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["counts"] = {"checks": len(report["checks"]), "passed": sum(item["passed"] for item in report["checks"]),
            "processes_started": len(group.items), "all_processes_exited": all(item["exited"] for item in report["cleanup"])}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="独立中心与四个合成微服务的真实HTTP并发验收")
    parser.add_argument("--serve", choices=("center", "module"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--report", type=Path, default=WORKSPACE / "outputs/coordination-20261028/process-acceptance.json")
    args = parser.parse_args()
    if args.serve:
        if not args.config:
            parser.error("--serve requires --config")
        serve(args.serve, args.config)
    else:
        directory = args.work_directory or WORKSPACE / "work/coordination-process-acceptance" / uuid.uuid4().hex
        result = run_acceptance(directory, args.report)
        print(json.dumps({"status": result["status"], "counts": result["counts"], "timings": result["timings"], "report": str(args.report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
