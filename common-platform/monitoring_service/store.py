"""Independent latest-value store and bounded service health aggregation."""
import asyncio
from contextlib import contextmanager
from hashlib import sha256
import json
import secrets
import sqlite3
import threading
import time

import httpx

from app.model_gateway import ModelGateway
from app.service_monitor import ServiceMonitor
from .protocol import MAX_INSTANCES, REPORT_TTL_SECONDS
from .settings import seal, unseal


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class MonitorError(Exception):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


class MonitorStore:
    def __init__(self, directory, *, transport=None, clock=None):
        self.directory = directory
        self.clock = clock or time.time
        self.transport = transport
        self.lock = threading.RLock()
        self.bootstrap = json.loads((directory / "monitor-bootstrap.json").read_text(encoding="utf-8"))
        self.control_token = unseal(directory, self.bootstrap["secret"])
        self.path = directory / "monitor.sqlite3"
        self.monitor = ServiceMonitor(self, clock=self.clock)
        with self.database() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, digest TEXT NOT NULL);
              INSERT OR IGNORE INTO control VALUES (1,0,'');
              CREATE TABLE IF NOT EXISTS targets (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, base_url TEXT NOT NULL,
                probe_secret TEXT NOT NULL, reporting_secret TEXT NOT NULL, reporting_hash TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reports (service_id TEXT NOT NULL, instance_id TEXT NOT NULL, revision INTEGER NOT NULL,
                boot_id TEXT NOT NULL, sequence INTEGER NOT NULL, received REAL NOT NULL, body TEXT NOT NULL,
                retired TEXT NOT NULL, PRIMARY KEY(service_id,instance_id));
            """)
        self.probe_requests = 0
        self.accepted_reports = 0
        self.snapshot_reads = 0

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def authorize(self, token):
        if not isinstance(token, str) or not secrets.compare_digest(token, self.control_token):
            raise MonitorError("monitor_auth", 401)

    def _target(self, db, service_id):
        target = db.execute("SELECT * FROM targets WHERE id=?", (service_id,)).fetchone()
        if target is None:
            raise MonitorError("monitor_target_missing", 404)
        return dict(target)

    def replace_targets(self, command):
        if command.owner_id != self.bootstrap["owner_id"]:
            raise MonitorError("monitor_owner", 403)
        targets = [t.model_dump() for t in command.targets]
        if len({t["id"] for t in targets}) != len(targets):
            raise MonitorError("monitor_duplicate_target")
        for target in targets:
            try:
                normalized = ModelGateway._base_url(target["base_url"])
            except Exception:
                raise MonitorError("monitor_target_url") from None
            if normalized != target["base_url"].rstrip("/"):
                raise MonitorError("monitor_target_url")
            target["base_url"] = normalized
        fingerprint = digest(targets)
        with self.lock:
            rebinds = []
            with self.database() as db:
                control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
                # An identical retry is safe after an interrupted control response.
                if fingerprint == control["digest"]:
                    return {"revision": control["revision"], "changed": False}
                if command.expected_revision != control["revision"]:
                    raise MonitorError("monitor_revision", 409)
                existing = {row["id"]: dict(row) for row in db.execute("SELECT * FROM targets")}
                for target in targets:
                    previous = existing.pop(target["id"], None)
                    if previous and target["revision"] < previous["revision"]:
                        raise MonitorError("monitor_target_revision", 409)
                    same_endpoint = bool(previous and previous["base_url"] == target["base_url"] and
                                         unseal(self.directory, json.loads(previous["probe_secret"])) == target["token"])
                    report_token = (unseal(self.directory, json.loads(previous["reporting_secret"]))
                                    if same_endpoint else secrets.token_urlsafe(48))
                    probe_secret = (previous["probe_secret"] if same_endpoint else
                                    json.dumps(seal(self.directory, target["token"])))
                    report_secret = (previous["reporting_secret"] if same_endpoint else
                                     json.dumps(seal(self.directory, report_token)))
                    db.execute("INSERT OR REPLACE INTO targets VALUES (?,?,?,?,?,?)",
                               (target["id"], target["revision"], target["base_url"], probe_secret, report_secret,
                                sha256(report_token.encode()).hexdigest()))
                    if same_endpoint:
                        rebinds.append((target["id"], previous["revision"], target["revision"]))
                        db.execute("UPDATE reports SET revision=? WHERE service_id=?", (target["revision"], target["id"]))
                    else:
                        db.execute("DELETE FROM reports WHERE service_id=?", (target["id"],))
                for removed in existing:
                    db.execute("DELETE FROM targets WHERE id=?", (removed,))
                    db.execute("DELETE FROM reports WHERE service_id=?", (removed,))
                db.execute("UPDATE control SET revision=revision+1,digest=? WHERE id=1", (fingerprint,))
                result = {"revision": control["revision"] + 1, "changed": True}
            # In-memory state moves only after the control transaction commits.
            for binding in rebinds:
                self.monitor.rebind_revision(*binding)
            for removed in existing:
                self.monitor.forget(removed)
            return result

    def reporting_credentials(self, service_id):
        with self.lock, self.database() as db:
            target = self._target(db, service_id)
            return {"monitor_version": "1.0", "service_id": service_id,
                    "url": self.bootstrap["url"] + "/api/v1/monitor/reports/" + service_id,
                    "token": unseal(self.directory, json.loads(target["reporting_secret"])),
                    "interval_seconds": 15}

    def authorize_report(self, service_id, token):
        with self.lock, self.database() as db:
            target = self._target(db, service_id)
            if not secrets.compare_digest(sha256(token.encode()).hexdigest(), target["reporting_hash"]):
                raise MonitorError("monitor_report_auth", 401)
            return target["revision"]

    def report(self, service_id, token, report):
        body = report.model_dump()
        serialized = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        now = self.clock()
        with self.lock, self.database() as db:
            target = self._target(db, service_id)
            if not secrets.compare_digest(sha256(token.encode()).hexdigest(), target["reporting_hash"]):
                raise MonitorError("monitor_report_auth", 401)
            # Latest values only. Long-expired identities cannot fill storage forever.
            db.execute("DELETE FROM reports WHERE received<?", (now - 3600,))
            previous = db.execute("SELECT * FROM reports WHERE service_id=? AND instance_id=?",
                                  (service_id, report.instance_id)).fetchone()
            retired = []
            if previous:
                retired = json.loads(previous["retired"])
                if report.boot_id in retired:
                    raise MonitorError("monitor_retired_boot", 409)
                if previous["boot_id"] == report.boot_id:
                    if report.sequence == previous["sequence"] and serialized == previous["body"]:
                        return {"accepted": True, "duplicate": True}
                    if report.sequence <= previous["sequence"]:
                        raise MonitorError("monitor_sequence", 409)
                else:
                    if len(retired) >= 16:
                        raise MonitorError("monitor_boot_limit", 429)
                    retired.append(previous["boot_id"])
                if now - previous["received"] < 1:
                    raise MonitorError("monitor_rate_limit", 429)
            else:
                count = db.execute("SELECT count(*) FROM reports WHERE service_id=?", (service_id,)).fetchone()[0]
                if count >= MAX_INSTANCES:
                    raise MonitorError("monitor_instance_limit", 429)
            db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?)",
                       (service_id, report.instance_id, target["revision"], report.boot_id, report.sequence,
                        now, serialized, json.dumps(retired),))
            self.accepted_reports += 1
            return {"accepted": True, "duplicate": False}

    def _monitor_targets(self):
        with self.lock, self.database() as db:
            return [row[0] for row in db.execute("SELECT id FROM targets ORDER BY id")]

    def _monitor_revision(self, service_id):
        with self.lock, self.database() as db:
            return self._target(db, service_id)["revision"]

    async def _probe_health(self, service_id, expected_revision):
        with self.lock, self.database() as db:
            target = self._target(db, service_id)
            if target["revision"] != expected_revision:
                raise MonitorError("monitor_target_revision", 409)
            token = unseal(self.directory, json.loads(target["probe_secret"]))
        started = self.clock()
        ok = False
        try:
            async with asyncio.timeout(ServiceMonitor.TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                             timeout=ServiceMonitor.TIMEOUT_SECONDS, follow_redirects=False) as client:
                    self.probe_requests += 1
                    async with client.stream("GET", target["base_url"] + "/platform/v1/health",
                                             headers={"Authorization": "Bearer " + token} if token else {}) as response:
                        if response.status_code == 200:
                            raw = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=1024):
                                if len(raw) + len(chunk) > 8192:
                                    raise ValueError()
                                raw.extend(chunk)
                            value = json.loads(raw)
                            if isinstance(value, dict):
                                valid = (("ok" not in value or type(value["ok"]) is bool) and
                                         ("status" not in value or isinstance(value["status"], str)))
                                healthy = value.get("ok") is True or value.get("status") in ("ok", "healthy")
                                bad = value.get("ok") is False or value.get("status") in ("error", "failed", "unhealthy", "offline", "degraded")
                                ok = valid and healthy and not bad
        except (httpx.HTTPError, ValueError, TimeoutError, UnicodeError, RecursionError):
            pass
        result = {"ok": ok, "message": "远程服务连接正常。" if ok else "远程健康检查未通过。",
                  "latency_ms": max(0, round((self.clock() - started) * 1000))}
        with self.lock, self.database() as db:
            if self._target(db, service_id)["revision"] != expected_revision:
                raise MonitorError("monitor_target_revision", 409)
            self.monitor.record(service_id, expected_revision, **result)
        return result

    def snapshot(self):
        now = self.clock()
        with self.lock, self.database() as db:
            self.snapshot_reads += 1
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            items = []
            counts = {key: 0 for key in ("healthy", "degraded", "offline", "stale", "unknown")}
            for target in db.execute("SELECT id,revision FROM targets ORDER BY id"):
                health = self.monitor.status(target["id"], target["revision"])
                counts[health["state"]] += 1
                rows = db.execute("SELECT * FROM reports WHERE service_id=? AND revision=?",
                                  (target["id"], target["revision"])).fetchall()
                fresh = [json.loads(row["body"]) for row in rows if 0 <= now-row["received"] <= REPORT_TTL_SECONDS]
                metrics = {}
                for key in ("cpu_percent", "rss_bytes", "inflight_requests", "queue_depth", "requests_total", "errors_total"):
                    values = [r["metrics"][key] for r in fresh if r["metrics"][key] is not None]
                    metrics[key] = ((sum(values)/len(values) if key == "cpu_percent" else sum(values))
                                    if values and len(values) == len(fresh) else None)
                latest = max((row["received"] for row in rows), default=None)
                items.append({"id": target["id"], "revision": target["revision"], "monitor": health,
                              "load": {"state": "fresh" if fresh else "stale" if rows else "unknown",
                                       "last_received_at": latest, "fresh_instances": len(fresh),
                                       "reported_instances": len(rows), "metrics": metrics}})
            return {"monitor_version": "1.0", "owner_id": self.bootstrap["owner_id"],
                    "collector_id": self.bootstrap["collector_id"], "generated_at": now,
                    "revision": control["revision"], "targets_digest": control["digest"],
                    "summary": {"total": len(items), **counts}, "items": items,
                    "counters": {"accepted_reports": self.accepted_reports, "health_requests": self.probe_requests,
                                 "snapshot_reads": self.snapshot_reads}}
