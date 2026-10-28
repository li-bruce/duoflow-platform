"""Reliable coordination 1.0 client for a module-owned sqlite3 database.

Call initialize outside transactions. enqueue never commits: call it inside the
same transaction as the business mutation. consume owns a short transaction,
calls a synchronous handler, commits the inbox and business writes, then ACKs.
External databases need an equivalent native outbox/inbox adapter; placing this
SQLite file beside PostgreSQL does not make their writes atomic.
"""
import hashlib
import inspect
import json
import math
import re
import sqlite3
import time
from urllib.parse import urlsplit

import httpx


class CoordinationError(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(code)


def _require(condition, code="coordination_contract"):
    if not condition:
        raise CoordinationError(code)


def _name(value, maximum=160, *, colon=True):
    return (isinstance(value, str) and 1 <= len(value) <= maximum
            and re.fullmatch(r"[A-Za-z0-9_.:-]+" if colon else r"[A-Za-z0-9_.-]+", value) is not None)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scope(value):
    return (isinstance(value, dict) and set(value) == {"organization_id", "project_id"}
            and all(_name(v, 128) for v in value.values()))


def validate_event(event, *, delivery=False):
    fields = {"version", "event_id", "module_id", "kind", "machine_id", "object_ref", "state", "revision", "scope"}
    kinds = {"state.changed", "publication.requested", "resource.published", "resource.withdrawn"}
    if delivery:
        kinds |= {"publication.approved", "publication.rejected"}
    _require(isinstance(event, dict) and fields <= set(event) <= fields | {"approval_ref"})
    _require(event["version"] == "1.0" and event["kind"] in kinds)
    _require(_name(event["event_id"]) and _name(event["module_id"], 128, colon=False)
             and _name(event["machine_id"], colon=False) and _name(event["state"], 128, colon=False))
    obj = event["object_ref"]
    _require(isinstance(obj, dict) and set(obj) == {"object_type", "object_id"}
             and _name(obj["object_type"], colon=False) and _name(obj["object_id"], 256))
    _require(type(event["revision"]) is int and 1 <= event["revision"] <= 2**53-1 and _scope(event["scope"]))
    if "approval_ref" in event:
        _require(_name(event["approval_ref"]))
    if event["kind"] in {"publication.approved", "publication.rejected"}:
        _require("approval_ref" in event)
    try:
        _require(len(_canonical(event).encode("utf-8")) <= 8192)
    except (ValueError, TypeError):
        raise CoordinationError("coordination_contract") from None
    return event


def initialize(conn):
    _require(isinstance(conn, sqlite3.Connection) and not conn.in_transaction, "coordination_transaction")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS duoflow_coord_outbox (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
            object_key TEXT NOT NULL, event_json TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            last_error TEXT);
        CREATE TABLE IF NOT EXISTS duoflow_coord_inbox (
            delivery_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, committed_at REAL NOT NULL);
    """)


def enqueue(conn, event):
    """Write an event inside the caller's active transaction, without committing."""
    _require(conn.in_transaction, "coordination_transaction")
    validate_event(event)
    encoded = _canonical(event)
    row = conn.execute("SELECT event_json FROM duoflow_coord_outbox WHERE event_id=?", (event["event_id"],)).fetchone()
    if row:
        _require(row[0] == encoded, "coordination_event_conflict")
        return False
    key = _canonical([event["module_id"], event["machine_id"], event["object_ref"]])
    conn.execute("INSERT INTO duoflow_coord_outbox(event_id,object_key,event_json) VALUES (?,?,?)", (event["event_id"], key, encoded))
    return True


class CoordinationClient:
    def __init__(self, *, base_url, token, module_id, consumer_id, scopes,
                 accepted_sources=None, timeout_seconds=3, transport=None, clock=time.time):
        parsed = urlsplit(base_url)
        _require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path.rstrip("/") == "/api/v1/coordination"
                 and (parsed.scheme == "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}), "coordination_configuration")
        _require(isinstance(token, str) and 16 <= len(token) <= 512 and not re.search(r"[\r\n ]", token)
                 and _name(module_id, 128, colon=False) and _name(consumer_id, 128), "coordination_configuration")
        _require(type(timeout_seconds) in {int, float} and math.isfinite(timeout_seconds)
                 and .05 <= timeout_seconds <= 30, "coordination_configuration")
        _require(isinstance(scopes, list) and 0 < len(scopes) <= 100 and all(_scope(v) for v in scopes), "coordination_configuration")
        accepted_sources = [module_id] if accepted_sources is None else accepted_sources
        _require(isinstance(accepted_sources, list) and 0 < len(accepted_sources) <= 100
                 and all(_name(v, 128, colon=False) for v in accepted_sources), "coordination_configuration")
        self.base_url, self._token = base_url.rstrip("/"), token
        self.module_id, self.consumer_id = module_id, consumer_id
        self.scopes = json.loads(_canonical(scopes))
        self.accepted_sources = frozenset(accepted_sources)
        self.timeout, self.transport, self.clock = timeout_seconds, transport, clock

    def _request(self, method, suffix, payload=None):
        try:
            started = time.monotonic()
            with httpx.Client(transport=self.transport, timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
                with client.stream(method, self.base_url + suffix, headers={"Authorization": "Bearer " + self._token}, json=payload) as response:
                    if not response.is_success:
                        raise CoordinationError("coordination_http", response.status_code)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        _require(size <= 262144, "coordination_response_limit")
                        _require(time.monotonic()-started <= self.timeout, "coordination_timeout")
                        chunks.append(chunk)
                    result = json.loads(b"".join(chunks))
                    _require(isinstance(result, dict))
                    return result
        except CoordinationError:
            raise
        except httpx.TimeoutException:
            raise CoordinationError("coordination_timeout") from None
        except Exception:
            raise CoordinationError("coordination_transport") from None

    def enqueue(self, conn, event):
        validate_event(event)
        _require(event["module_id"] == self.module_id and event["scope"] in self.scopes, "coordination_scope")
        return enqueue(conn, event)

    def relay(self, conn, *, limit=20):
        """Send at most limit committed events. Retry metadata is durable."""
        _require(not conn.in_transaction, "coordination_transaction")
        _require(type(limit) is int and 1 <= limit <= 20)
        rows = conn.execute("""SELECT o.event_id,o.event_json,o.attempts FROM duoflow_coord_outbox o
            WHERE o.sent=0 AND o.next_attempt<=? AND NOT EXISTS (
                SELECT 1 FROM duoflow_coord_outbox p WHERE p.sent=0 AND p.object_key=o.object_key AND p.sequence<o.sequence)
            ORDER BY o.sequence LIMIT ?""", (self.clock(), limit)).fetchall()
        sent = failed = 0
        for event_id, encoded, attempts in rows:
            event = json.loads(encoded)
            try:
                validate_event(event)
                _require(event["module_id"] == self.module_id and event["scope"] in self.scopes, "coordination_scope")
                result = self._request("POST", "/events", event)
                _require(result.get("accepted") is True and type(result.get("duplicate")) is bool and result.get("event_id") == event_id)
            except CoordinationError as error:
                with conn:
                    conn.execute("UPDATE duoflow_coord_outbox SET attempts=attempts+1,next_attempt=?,last_error=? WHERE event_id=? AND sent=0",
                                 (self.clock()+min(300, 2**min(attempts, 8)), error.code, event_id))
                failed += 1
            else:
                with conn:
                    conn.execute("UPDATE duoflow_coord_outbox SET sent=1,attempts=attempts+1,last_error=NULL WHERE event_id=?", (event_id,))
                sent += 1
        return {"sent": sent, "failed": failed}

    def _delivery(self, item):
        _require(isinstance(item, dict) and _name(item.get("id")) and _name(item.get("subscription_id"), 256))
        _require(item.get("target_module") == self.module_id and item.get("consumer_id") == self.consumer_id, "coordination_target")
        _require(item.get("mode") in {"notify", "sync"} and isinstance(item.get("lease_token"), str)
                 and 16 <= len(item["lease_token"]) <= 256 and re.fullmatch(r"[A-Za-z0-9_-]+", item["lease_token"]))
        _require(type(item.get("lease_until")) in {int, float} and math.isfinite(item["lease_until"]) and item["lease_until"] > 0
                 and type(item.get("attempts")) is int and item["attempts"] >= 1)
        event = validate_event(item.get("event"), delivery=True)
        _require(event["module_id"] in self.accepted_sources and event["scope"] in self.scopes, "coordination_scope")
        return item

    def pull(self, *, limit=20):
        _require(type(limit) is int and 1 <= limit <= 20)
        result = self._request("GET", f"/deliveries?consumer_id={self.consumer_id}&limit={limit}")
        _require(result.get("version") == "1.0" and result.get("module_id") == self.module_id
                 and result.get("consumer_id") == self.consumer_id and isinstance(result.get("deliveries"), list)
                 and len(result["deliveries"]) <= limit)
        return [self._delivery(item) for item in result["deliveries"]]

    def consume(self, conn, item, handler):
        """Apply once in SQLite; ACK only after commit. Re-delivery retries ACK."""
        self._delivery(item)
        _require(not conn.in_transaction and callable(handler), "coordination_transaction")
        _require(not inspect.iscoroutinefunction(handler) and not inspect.isasyncgenfunction(handler), "coordination_handler_transaction")
        # Lease/attempts change on redelivery; identity and event must not change.
        identity = {k: item[k] for k in ["id", "subscription_id", "target_module", "mode", "event"]}
        fingerprint = hashlib.sha256(_canonical(identity).encode()).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT fingerprint FROM duoflow_coord_inbox WHERE delivery_id=?", (item["id"],)).fetchone()
            if row:
                _require(row[0] == fingerprint, "coordination_delivery_conflict")
            else:
                outcome = handler(conn, item["event"], item)
                if inspect.isawaitable(outcome) and hasattr(outcome, "close"):
                    outcome.close()
                _require(not inspect.isawaitable(outcome) and conn.in_transaction, "coordination_handler_transaction")
                conn.execute("INSERT INTO duoflow_coord_inbox VALUES (?,?,?)", (item["id"], fingerprint, self.clock()))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        try:
            result = self._request("POST", "/deliveries/" + item["id"] + "/ack",
                                   {"consumer_id": self.consumer_id, "lease_token": item["lease_token"]})
            _require(result.get("acknowledged") is True and type(result.get("duplicate")) is bool and result.get("delivery_id") == item["id"])
        except CoordinationError as error:
            return {"applied": row is None, "acknowledged": False, "error": error.code}
        return {"applied": row is None, "acknowledged": True}
