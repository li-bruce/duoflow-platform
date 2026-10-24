"""Receive bounded module observations without participating in business commits.

The separate SQLite store prevents callback writes from waiting on the platform
transaction that is itself waiting for a remote capability. Observations carry
registered identities and state references, never arbitrary input/output data.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Request
from jsonschema import Draft202012Validator

from .access_bridge import AccessBridge
from .errors import require
from .identity import authorization_snapshot, refresh_actor
from .registration_contract import digest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeObservations:
    """Call-scoped tickets, ordered observations and explicit missing evidence."""

    def __init__(self, runtime, *, clock=None):
        self.runtime = runtime
        self.clock = clock or time.time
        self.path = runtime.data_dir / "observations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.validator = Draft202012Validator(
            json.loads(
                (ROOT / "contracts/runtime-observation/v1/batch.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        with self.database() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    request_id TEXT PRIMARY KEY, ticket_hash TEXT NOT NULL,
                    claims TEXT NOT NULL, graph TEXT NOT NULL,
                    expires REAL NOT NULL, received REAL NOT NULL, dropped INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    request_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event TEXT NOT NULL, received REAL NOT NULL,
                    PRIMARY KEY(request_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS admission_probes (
                    ticket_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL,
                    service_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    expires REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0
                );
            """
            )

    @contextmanager
    def database(self):
        """Close every connection; bounded transactions protect threaded callbacks."""
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _current(self, actor, claims):
        """Recheck current identity, service revision and all scoped grants."""
        with self.runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
        require(
            authorization_snapshot(current) == claims["actor"],
            "observation_authorization",
            "运行观察的身份已经变化。",
            403,
        )
        spec = self.runtime.registry.get(claims["binding"]["id"], current)
        require(
            AccessBridge._binding(spec) == claims["binding"],
            "observation_binding",
            "运行观察的服务版本已经变化。",
            409,
        )
        require(
            self.runtime.availability(spec)["available"],
            "observation_unavailable",
            "服务当前不可用。",
            503,
        )
        require(
            self.runtime.access.authorization_stamp(current) == claims["stamp"],
            "observation_authorization",
            "运行观察的授权已经变化。",
            403,
        )
        return current

    def begin_probe(self, service_id, revision):
        """Create a one-minute challenge for an authenticated module handshake."""
        ticket, nonce = secrets.token_hex(32), secrets.token_hex(16)
        with self.database() as db:
            db.execute("DELETE FROM admission_probes WHERE expires < ?", (self.clock(),))
            db.execute(
                "INSERT INTO admission_probes VALUES(?,?,?,?,?,0)",
                (
                    hashlib.sha256(ticket.encode()).hexdigest(),
                    nonce,
                    service_id,
                    revision,
                    self.clock() + 60,
                ),
            )
        return {"ticket": ticket, "nonce": nonce}

    def accept_probe(self, ticket, body):
        """Prove the module can call the configured platform observation endpoint."""
        require(
            isinstance(ticket, str) and re.fullmatch(r"[0-9a-f]{64}", ticket) is not None,
            "observation_probe",
            "运行同步验证凭据无效。",
            401,
        )
        require(
            isinstance(body, dict)
            and set(body) == {"runtime_version", "nonce"}
            and body["runtime_version"] == "1.0",
            "observation_probe",
            "运行同步验证格式无效。",
            422,
        )
        hashed = hashlib.sha256(ticket.encode()).hexdigest()
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM admission_probes WHERE ticket_hash=?", (hashed,)
            ).fetchone()
            require(
                row is not None and row["nonce"] == body["nonce"] and self.clock() < row["expires"],
                "observation_probe",
                "运行同步验证已失效。",
                401,
            )
            binding = self.runtime.remote_services.get_binding(row["service_id"])
            require(
                binding["revision"] == row["revision"],
                "observation_binding",
                "服务连接已变化。",
                409,
            )
            db.execute("UPDATE admission_probes SET confirmed=1 WHERE ticket_hash=?", (hashed,))
        return {"accepted": True, "nonce": body["nonce"]}

    def require_probe(self, challenge):
        """A module acknowledgement alone cannot substitute for a received callback."""
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM admission_probes WHERE ticket_hash=?",
                (hashlib.sha256(challenge["ticket"].encode()).hexdigest(),),
            ).fetchone()
        require(
            row is not None
            and row["confirmed"] == 1
            and row["nonce"] == challenge["nonce"]
            and self.clock() < row["expires"],
            "observation_probe",
            "模块尚未完成运行同步接口确认。",
            409,
        )

    def issue(self, actor, spec, request_id, graph):
        """Issue a private callback ticket bound to this user, service and call."""
        require(
            re.fullmatch(r"[0-9a-f]{32}", request_id) is not None,
            "observation_request",
            "调用标识无效。",
            422,
        )
        claims = {
            "actor": authorization_snapshot(actor),
            "stamp": self.runtime.access.authorization_stamp(actor),
            "binding": AccessBridge._binding(spec),
        }
        self._current(actor, claims)
        ticket = secrets.token_hex(32)
        with self.database() as db:
            # A replay keeps its earlier observations only for the exact same
            # call identity and graph; it receives a new short-lived ticket.
            old = db.execute(
                "SELECT claims, graph FROM calls WHERE request_id=?", (request_id,)
            ).fetchone()
            require(
                old is None
                or (json.loads(old["claims"]) == claims and json.loads(old["graph"]) == graph),
                "observation_conflict",
                "同一调用的观察定义发生变化。",
                409,
            )
            db.execute(
                "INSERT INTO calls(request_id,ticket_hash,claims,graph,expires,received) VALUES(?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET ticket_hash=excluded.ticket_hash,expires=excluded.expires",
                (
                    request_id,
                    hashlib.sha256(ticket.encode()).hexdigest(),
                    json.dumps(claims),
                    json.dumps(graph),
                    self.clock() + 120,
                    self.clock(),
                ),
            )
        return ticket

    def accept(self, ticket, batch):
        """Validate the whole batch before persisting any of its events."""
        require(
            isinstance(ticket, str) and re.fullmatch(r"[0-9a-f]{64}", ticket) is not None,
            "observation_ticket",
            "运行观察凭据无效。",
            401,
        )
        require(
            self.validator.is_valid(batch), "observation_contract", "运行观察数据不符合契约。", 422
        )
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM calls WHERE request_id=?", (batch["request_id"],)
            ).fetchone()
        require(
            row is not None
            and secrets.compare_digest(
                row["ticket_hash"], hashlib.sha256(ticket.encode()).hexdigest()
            )
            and self.clock() < row["expires"],
            "observation_ticket",
            "运行观察凭据已失效。",
            401,
        )
        claims, graph = json.loads(row["claims"]), json.loads(row["graph"])
        self._current(claims["actor"], claims)
        require(
            batch["graph_version"] == graph["version"],
            "observation_graph",
            "业务图版本不匹配。",
            409,
        )
        atoms = {a["id"]: a for a in graph["atoms"]}
        machines = {m["id"]: m for m in graph["state_machines"] if m["scope"] == "business"}
        seen = set()
        for event in batch["events"]:
            atom = atoms.get(event["atom_id"])
            require(
                atom is not None and atom["kind"] == "backend",
                "observation_atom",
                "服务观察需要已登记的后端原子。",
                422,
            )
            require(
                (event["parent_call_id"] is None) == (event["call_id"] == batch["request_id"]),
                "observation_parent",
                "根调用与子调用的关系不一致。",
                422,
            )
            if event["call_id"] == batch["request_id"]:
                require(
                    atom.get("capability", {}).get("id") == claims["binding"]["id"],
                    "observation_atom",
                    "根调用必须对应实际被调用的能力。",
                    422,
                )
            require(
                event["sequence"] not in seen and (event["parent_call_id"] != event["call_id"]),
                "observation_sequence",
                "调用序号或父子关系无效。",
                422,
            )
            seen.add(event["sequence"])
            require(
                (event["phase"] == "state") == ("state" in event),
                "observation_state",
                "状态事件需要正式状态引用。",
                422,
            )
            if "state" in event:
                state = event["state"]
                machine = machines.get(state["machine_id"])
                require(
                    machine is not None and state["value"] in {s["id"] for s in machine["states"]},
                    "observation_state",
                    "模块报告了未声明的正式状态。",
                    422,
                )
                require(
                    state["object_ref"]["object_type"].startswith(
                        claims["binding"]["module_id"] + "."
                    ),
                    "observation_object",
                    "正式对象引用必须属于当前模块。",
                    422,
                )
        with self.database() as db:
            for event in batch["events"]:
                old = db.execute(
                    "SELECT event FROM events WHERE request_id=? AND sequence=?",
                    (batch["request_id"], event["sequence"]),
                ).fetchone()
                require(
                    old is None or json.loads(old["event"]) == event,
                    "observation_conflict",
                    "重复序号包含不同观察。",
                    409,
                )
                db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                    (batch["request_id"], event["sequence"], json.dumps(event), self.clock()),
                )
            db.execute(
                "UPDATE calls SET received=?,dropped=MAX(dropped,?) WHERE request_id=?",
                (self.clock(), batch["dropped"], batch["request_id"]),
            )
        return {"accepted": True, "received": len(batch["events"])}

    async def read(self, actor, request_id):
        """Read one's observations, rechecking current object access before return."""
        with self.database() as db:
            row = db.execute("SELECT * FROM calls WHERE request_id=?", (request_id,)).fetchone()
            records = db.execute(
                "SELECT event FROM events WHERE request_id=? ORDER BY sequence", (request_id,)
            ).fetchall()
        require(row is not None, "not_found", "没有该调用的运行观察。", 404)
        claims, graph = json.loads(row["claims"]), json.loads(row["graph"])
        require(
            actor["id"] == claims["actor"]["id"], "forbidden", "无权读取其他人的运行观察。", 403
        )
        self._current(actor, claims)
        events = [json.loads(record["event"]) for record in records]
        objects = {
            digest(e["state"]["object_ref"]): e["state"]["object_ref"]
            for e in events
            if "state" in e
        }
        for ref in objects.values():
            flow = next(
                (
                    w
                    for w in self.runtime.workflows.definitions.values()
                    if w["provider"]["object_type"] == ref["object_type"]
                    and w["id"].startswith(claims["binding"]["module_id"] + ".")
                ),
                None,
            )
            require(flow is not None, "observation_object", "业务对象缺少读取入口。", 403)
            await self.runtime.workflows.transport.request(
                actor, flow["provider"]["read_capability"], "objects.read", {"object_ref": ref}
            )
        self._current(actor, claims)
        sequences = {e["sequence"] for e in events}
        gaps = [
            number for number in range(1, max(sequences, default=0) + 1) if number not in sequences
        ]
        completed = any(
            e["call_id"] == request_id and e["phase"] in {"succeeded", "failed"} for e in events
        )
        freshness = (
            "complete"
            if completed
            else "stale" if self.clock() - row["received"] > 30 else "active" if events else "empty"
        )
        return {
            "request_id": request_id,
            "graph_version": graph["version"],
            "events": events,
            "gaps": gaps,
            "dropped": row["dropped"],
            "freshness": freshness,
            "received_at": row["received"],
        }


def create_observation_router(observations, require_user):
    """Separate private service callbacks from authenticated browser reads."""
    router = APIRouter(prefix="/api/v1/runtime-observations")

    @router.post("/events")
    async def receive(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            require(len(raw) <= 32768, "observation_size", "运行观察批次超过大小限制。", 413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            require(False, "observation_contract", "运行观察格式无效。", 422)
        return observations.accept(request.headers.get("X-DuoFlow-Observation"), body)

    @router.post("/probe")
    async def probe(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            require(len(raw) <= 1024, "observation_size", "运行同步验证超过大小限制。", 413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            require(False, "observation_probe", "运行同步验证格式无效。", 422)
        return observations.accept_probe(request.headers.get("X-DuoFlow-Observation"), body)

    from fastapi import Depends

    @router.get("/{request_id}")
    async def read(request_id: str, actor=Depends(require_user)):
        return await observations.read(actor, request_id)

    return router
