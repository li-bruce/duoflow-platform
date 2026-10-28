"""Transactional event admission, approval and leased fan-out, separate from API runtime."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .protocol import Config, Decision, Event, CoordinationError, LEASE_SECONDS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(condition, code, status=422):
    if not condition:
        raise CoordinationError(code, status)


class CoordinationStore:
    def __init__(self, directory, *, clock=None, control_token=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "coordination.sqlite3"
        self.clock = clock or time.time
        if control_token is None:
            from .settings import load_control_token
            control_token = load_control_token(self.directory)
        require(isinstance(control_token, str) and 16 <= len(control_token) <= 512, "coordination_configuration")
        self.control_hash = digest(control_token)
        with self.database() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS modules(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL,declaration TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,source_module TEXT NOT NULL,target_module TEXT NOT NULL,declaration TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,event TEXT NOT NULL,received REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS states(object_key TEXT PRIMARY KEY,module_id TEXT NOT NULL,machine_id TEXT NOT NULL,object_type TEXT NOT NULL,object_id TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,scope TEXT NOT NULL,event_id TEXT NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(event_id TEXT PRIMARY KEY,request TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,actor_id TEXT,created_at REAL NOT NULL,decided_at REAL,used_by TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,event_id TEXT NOT NULL,subscription_id TEXT NOT NULL,target_module TEXT NOT NULL,mode TEXT NOT NULL,event TEXT NOT NULL,object_key TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,consumer_id TEXT,lease_hash TEXT,lease_until REAL,created_at REAL NOT NULL,ordinal INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS delivery_pull ON deliveries(target_module,status,ordinal);
            CREATE INDEX IF NOT EXISTS delivery_order ON deliveries(target_module,object_key,ordinal,status);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES('config_revision','0')")
            db.execute("INSERT OR IGNORE INTO metadata VALUES('control_hash',?)", (self.control_hash,))
            require(db.execute("SELECT value FROM metadata WHERE key='control_hash'").fetchone()[0] == self.control_hash,
                    "coordination_configuration", 409)
            bootstrap_path = self.directory / "coordination-bootstrap.json"
            configured_id = json.loads(bootstrap_path.read_text(encoding="utf-8")).get("center_id") if bootstrap_path.exists() else None
            if configured_id is not None:
                import re
                require(isinstance(configured_id, str) and re.fullmatch(r"[a-f0-9]{32}", configured_id) is not None,
                        "coordination_configuration")
            db.execute("INSERT OR IGNORE INTO metadata VALUES('center_id',?)", (configured_id or uuid.uuid4().hex,))
            self.center_id = db.execute("SELECT value FROM metadata WHERE key='center_id'").fetchone()[0]
            require(configured_id is None or configured_id == self.center_id, "coordination_configuration", 409)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def authorize(self, token):
        require(isinstance(token, str) and secrets.compare_digest(digest(token), self.control_hash), "coordination_authentication", 401)

    def _module(self, db, token, module_id=None):
        require(isinstance(token, str) and token != "", "coordination_authentication", 401)
        module = db.execute("SELECT * FROM modules WHERE token_hash=?", (digest(token),)).fetchone()
        require(module is not None, "coordination_authentication", 401)
        require(module["enabled"] == 1, "coordination_module_disabled", 403)
        require(module_id is None or module["id"] == module_id, "coordination_module_identity", 403)
        return json.loads(module["declaration"])

    def authorize_module(self, token, module_id=None):
        with self.database() as db:
            return self._module(db, token, module_id)

    @staticmethod
    def object_key(event):
        return canonical([event["module_id"], event["machine_id"], event["object_ref"]["object_type"], event["object_ref"]["object_id"]])

    @staticmethod
    def _machine(module, event):
        machine = next((m for m in module["machines"] if m["id"] == event["machine_id"]), None)
        require(machine is not None and event["object_ref"]["object_type"] in machine["object_types"]
                and event["state"] in machine["states"], "coordination_declaration")
        require(event["kind"] in module["emits"], "coordination_emission", 403)
        return machine

    @staticmethod
    def _declared(module, event):
        """Configuration edits immediately withdraw queued obsolete declarations."""
        if module is None:
            return False
        machine = next((m for m in module["machines"] if m["id"] == event["machine_id"]), None)
        return bool(machine and event["object_ref"]["object_type"] in machine["object_types"] and event["state"] in machine["states"])

    def configure(self, token, command):
        self.authorize(token)
        command = command if isinstance(command, Config) else Config.model_validate(command)
        require(all(digest(m.token) != self.control_hash for m in command.modules), "coordination_credential_separation")
        modules = {m.id: m.model_dump(exclude={"token"}) for m in command.modules}
        subscriptions = {s.id: s.model_dump() for s in command.subscriptions}
        with self.database() as db:
            revision = int(db.execute("SELECT value FROM metadata WHERE key='config_revision'").fetchone()[0])
            require(command.expected_revision == revision, "coordination_revision_conflict", 409)
            old_subs = {r["id"]: json.loads(r["declaration"]) for r in db.execute("SELECT * FROM subscriptions")}
            db.execute("DELETE FROM subscriptions")
            db.execute("DELETE FROM modules")
            for m in command.modules:
                db.execute("INSERT INTO modules VALUES(?,?,?,?)", (m.id, digest(m.token), int(m.enabled), canonical(modules[m.id])))
            for s in command.subscriptions:
                db.execute("INSERT INTO subscriptions VALUES(?,?,?,?)", (s.id, s.source_module, s.target_module, canonical(subscriptions[s.id])))
            for row in db.execute("SELECT * FROM deliveries WHERE status IN ('pending','leased')").fetchall():
                event = json.loads(row["event"])
                source, target = modules.get(event["module_id"]), modules.get(row["target_module"])
                active = source is not None and target is not None and source["enabled"] and target["enabled"]
                active = active and self._declared(source, event)
                if row["subscription_id"].startswith("approval:"):
                    active = active and event["module_id"] == row["target_module"] and "publication.requested" in source["emits"]
                else:
                    definition = subscriptions.get(row["subscription_id"])
                    active = active and definition is not None and definition == old_subs.get(row["subscription_id"])
                if not active:
                    db.execute("UPDATE deliveries SET status='cancelled',lease_hash=NULL,lease_until=NULL WHERE id=?", (row["id"],))
            db.execute("UPDATE metadata SET value=? WHERE key='config_revision'", (str(revision + 1),))
        return {"version": "1.0", "config_revision": revision + 1}

    def _enqueue(self, db, event, subscription_id, target, mode):
        identifier = uuid.uuid4().hex
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM deliveries").fetchone()[0]
        db.execute("INSERT INTO deliveries(id,event_id,subscription_id,target_module,mode,event,object_key,status,created_at,ordinal) VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                   (identifier, event["event_id"], subscription_id, target, mode, canonical(event), self.object_key(event), self.clock(), ordinal))
        return identifier

    def accept(self, token, command):
        event = command.model_dump(exclude_none=True) if isinstance(command, Event) else Event.model_validate(command).model_dump(exclude_none=True)
        encoded, now = canonical(event), self.clock()
        key = self.object_key(event)
        with self.database() as db:
            module = self._module(db, token, event["module_id"])
            machine = self._machine(module, event)
            previous = db.execute("SELECT * FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if previous:
                require(previous["fingerprint"] == digest(encoded), "coordination_event_conflict", 409)
                return {"accepted": True, "duplicate": True, "event_id": event["event_id"]}
            latest = db.execute("SELECT * FROM states WHERE object_key=?", (key,)).fetchone()
            if latest:
                require(event["revision"] >= latest["revision"], "coordination_stale_revision", 409)
                if event["revision"] == latest["revision"]:
                    require(event["state"] == latest["state"] and canonical(event["scope"]) == latest["scope"], "coordination_state_conflict", 409)
            public = event["state"] in machine["public_states"]
            require(event["kind"] != "resource.published" or public, "coordination_public_state")
            require(event["kind"] != "publication.requested" or not public, "coordination_approval_state")
            # All event kinds observe the same approval boundary: changing a kind
            # to state.changed cannot publish an otherwise unapproved projection.
            if public and machine["approval_required"]:
                require(event["kind"] == "resource.published", "coordination_approval_required", 403)
            if event["kind"] == "resource.published" and machine["approval_required"]:
                approval = db.execute("SELECT * FROM approvals WHERE event_id=?", (event.get("approval_ref"),)).fetchone()
                require(approval is not None and approval["status"] == "approved" and approval["used_by"] is None,
                        "coordination_approval_required", 403)
                request = json.loads(approval["request"])
                require(self.object_key(request) == key and request["scope"] == event["scope"]
                        and event["revision"] == request["revision"] + 1 and latest is not None
                        and latest["revision"] == request["revision"] and latest["state"] == request["state"],
                        "coordination_approval_stale", 409)
                db.execute("UPDATE approvals SET used_by=? WHERE event_id=?", (event["event_id"], approval["event_id"]))
            elif event.get("approval_ref") is not None:
                raise CoordinationError("coordination_approval_reference")
            db.execute("INSERT INTO events VALUES(?,?,?,?)", (event["event_id"], digest(encoded), encoded, now))
            db.execute("INSERT INTO states VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(object_key) DO UPDATE SET state=excluded.state,revision=excluded.revision,scope=excluded.scope,event_id=excluded.event_id,updated_at=excluded.updated_at",
                       (key, event["module_id"], event["machine_id"], event["object_ref"]["object_type"], event["object_ref"]["object_id"], event["state"], event["revision"], canonical(event["scope"]), event["event_id"], now))
            if event["kind"] == "publication.requested":
                db.execute("INSERT INTO approvals(event_id,request,status,revision,created_at) VALUES(?,?,'pending',1,?)", (event["event_id"], encoded, now))
            else:
                for row in db.execute("SELECT * FROM subscriptions WHERE source_module=?", (event["module_id"],)):
                    sub = json.loads(row["declaration"])
                    target = db.execute("SELECT enabled FROM modules WHERE id=?", (sub["target_module"],)).fetchone()
                    if target and target["enabled"] and sub["kind"] == event["kind"] and sub["scope"] == event["scope"]:
                        self._enqueue(db, event, sub["id"], sub["target_module"], sub["mode"])
        return {"accepted": True, "duplicate": False, "event_id": event["event_id"]}

    def decide(self, token, event_id, command):
        self.authorize(token)
        command = command if isinstance(command, Decision) else Decision.model_validate(command)
        with self.database() as db:
            row = db.execute("SELECT * FROM approvals WHERE event_id=?", (event_id,)).fetchone()
            require(row is not None, "coordination_approval_missing", 404)
            require(row["revision"] == command.expected_revision and row["status"] == "pending", "coordination_revision_conflict", 409)
            request = json.loads(row["request"])
            module = db.execute("SELECT * FROM modules WHERE id=?", (request["module_id"],)).fetchone()
            require(module is not None and module["enabled"], "coordination_module_disabled", 403)
            self._machine(json.loads(module["declaration"]), request)
            latest = db.execute("SELECT * FROM states WHERE object_key=?", (self.object_key(request),)).fetchone()
            require(latest is not None and latest["revision"] == request["revision"] and latest["state"] == request["state"]
                    and latest["scope"] == canonical(request["scope"]), "coordination_approval_stale", 409)
            db.execute("UPDATE approvals SET status=?,revision=revision+1,actor_id=?,decided_at=? WHERE event_id=?",
                       (command.decision, command.actor_id, self.clock(), event_id))
            event = {**request, "event_id": "decision-" + digest(event_id), "kind": "publication." + command.decision, "approval_ref": event_id}
            delivery_id = self._enqueue(db, event, "approval:" + event_id, request["module_id"], "sync")
            updated = db.execute("SELECT * FROM approvals WHERE event_id=?", (event_id,)).fetchone()
        return {"version": "1.0", "approval": self._approval(updated), "delivery_id": delivery_id}

    def pull(self, token, consumer_id, limit=20):
        import re
        require(isinstance(consumer_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", consumer_id) is not None,
                "coordination_consumer")
        require(type(limit) is int and 1 <= limit <= 20, "coordination_limit")
        now, deliveries = self.clock(), []
        with self.database() as db:
            module = self._module(db, token)
            rows = db.execute("""SELECT candidate.* FROM deliveries candidate
                WHERE candidate.target_module=?
                  AND (candidate.status='pending' OR (candidate.status='leased' AND candidate.lease_until<=?))
                  AND NOT EXISTS (
                    SELECT 1 FROM deliveries previous
                    WHERE previous.target_module=candidate.target_module AND previous.object_key=candidate.object_key
                      AND previous.ordinal<candidate.ordinal AND previous.status IN ('pending','leased')
                  ) ORDER BY candidate.ordinal LIMIT ?""", (module["id"], now, limit)).fetchall()
            for row in rows:
                lease = secrets.token_urlsafe(32)
                until = now + LEASE_SECONDS
                db.execute("UPDATE deliveries SET status='leased',attempts=attempts+1,consumer_id=?,lease_hash=?,lease_until=? WHERE id=?",
                           (consumer_id, digest(lease), until, row["id"]))
                deliveries.append({"id": row["id"], "subscription_id": row["subscription_id"], "target_module": module["id"], "mode": row["mode"],
                                   "event": json.loads(row["event"]), "consumer_id": consumer_id, "lease_token": lease,
                                   "lease_until": until, "attempts": row["attempts"] + 1})
        return {"version": "1.0", "module_id": module["id"], "consumer_id": consumer_id, "deliveries": deliveries}

    def ack(self, token, delivery_id, consumer_id, lease_token):
        now = self.clock()
        with self.database() as db:
            module = self._module(db, token)
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            require(row is not None and row["target_module"] == module["id"], "coordination_delivery_missing", 404)
            require(row["consumer_id"] == consumer_id and isinstance(lease_token, str)
                    and secrets.compare_digest(row["lease_hash"] or "", digest(lease_token)), "coordination_lease_conflict", 409)
            if row["status"] == "acked":
                return {"acknowledged": True, "duplicate": True, "delivery_id": delivery_id}
            require(row["status"] == "leased" and row["lease_until"] > now, "coordination_lease_conflict", 409)
            db.execute("UPDATE deliveries SET status='acked' WHERE id=?", (delivery_id,))
        return {"acknowledged": True, "duplicate": False, "delivery_id": delivery_id}

    @staticmethod
    def _approval(row):
        event = json.loads(row["request"])
        return {"event_id": row["event_id"], "module_id": event["module_id"], "machine_id": event["machine_id"],
                "object_ref": event["object_ref"], "state": event["state"], "object_revision": event["revision"], "scope": event["scope"],
                **{k: row[k] for k in ("status", "revision", "actor_id", "created_at", "decided_at", "used_by")}}

    def summary(self, token):
        self.authorize(token)
        with self.database() as db:
            modules = {r["id"]: json.loads(r["declaration"]) for r in db.execute("SELECT * FROM modules")}
            objects = []
            for row in db.execute("SELECT * FROM states ORDER BY updated_at DESC,object_key LIMIT 1000"):
                module = modules.get(row["module_id"])
                machine = next((m for m in module["machines"] if m["id"] == row["machine_id"]), None) if module else None
                if machine and row["object_type"] in machine["object_types"] and row["state"] in machine["states"]:
                    objects.append({"module_id": row["module_id"], "machine_id": row["machine_id"],
                                    "object_ref": {"object_type": row["object_type"], "object_id": row["object_id"]},
                                    "state": row["state"], "revision": row["revision"], "scope": json.loads(row["scope"]),
                                    "event_id": row["event_id"], "updated_at": row["updated_at"]})
                    if len(objects) == 100:
                        break
            counts = {"events": db.execute("SELECT count(*) FROM events").fetchone()[0], "objects": db.execute("SELECT count(*) FROM states").fetchone()[0],
                      "approvals_pending": db.execute("SELECT count(*) FROM approvals WHERE status='pending'").fetchone()[0]}
            for status in ("pending", "leased", "acked", "cancelled"):
                counts["deliveries_" + status] = db.execute("SELECT count(*) FROM deliveries WHERE status=?", (status,)).fetchone()[0]
            approvals = []
            for row in db.execute("SELECT * FROM approvals ORDER BY created_at DESC,event_id LIMIT 1000"):
                request = json.loads(row["request"])
                if self._declared(modules.get(request["module_id"]), request):
                    approvals.append(self._approval(row))
                    if len(approvals) == 100:
                        break
            backlogs = []
            for identifier in modules:
                values = {r["status"]: r["n"] for r in db.execute("SELECT status,count(*) n FROM deliveries WHERE target_module=? AND status IN ('pending','leased') GROUP BY status", (identifier,))}
                backlogs.append({"module_id": identifier, "pending": values.get("pending", 0), "leased": values.get("leased", 0)})
            revision = int(db.execute("SELECT value FROM metadata WHERE key='config_revision'").fetchone()[0])
        return {"version": "1.0", "config_revision": revision, "counts": counts, "objects": objects, "approvals": approvals, "backlogs": backlogs}

    def configuration(self, token):
        self.authorize(token)
        with self.database() as db:
            revision = int(db.execute("SELECT value FROM metadata WHERE key='config_revision'").fetchone()[0])
            modules = [json.loads(r["declaration"]) for r in db.execute("SELECT declaration FROM modules ORDER BY id")]
            subscriptions = [json.loads(r["declaration"]) for r in db.execute("SELECT declaration FROM subscriptions ORDER BY id")]
        return {"version": "1.0", "config_revision": revision, "modules": modules, "subscriptions": subscriptions}
