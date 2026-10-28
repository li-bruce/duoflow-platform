"""Link actual shared host instances with atomic definitions and browser presence.

Browser reports describe local editing/presentation only. Formal business state
always comes from the existing authorized workflow read path.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .errors import require


class PresenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    step_id: str | None = Field(default=None, max_length=80)
    surface: str = Field(pattern=r"^(page|conversation)$")
    status: str = Field(pattern=r"^(ready|editing|busy|error)$")
    revision: int = Field(ge=1)


class AtomicInstances:
    def __init__(self, runtime):
        self.runtime = runtime
        with runtime.observations.database() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS ui_presence (
                owner TEXT NOT NULL, kind TEXT NOT NULL, instance_id TEXT NOT NULL,
                atom_id TEXT NOT NULL, session_id TEXT NOT NULL, surface TEXT NOT NULL,
                status TEXT NOT NULL, revision INTEGER NOT NULL, received REAL NOT NULL,
                PRIMARY KEY(owner,kind,instance_id,atom_id,session_id)
            )"""
            )

    async def resolve(self, actor, kind, instance_id, *, business_read=True):
        """Load the real owned instance, then attach validated atomic identities."""
        require(kind in {"interaction", "workflow"}, "atomic_host", "组件宿主类型无效。", 422)
        if kind == "workflow":
            if business_read:
                item = await self.runtime.workflows.get(actor, instance_id)
            else:
                # A presence heartbeat reports only this browser's UI status.
                # load checks live identity, ownership, access stamp and host
                # definition without remote object/list IO or adopting a Run.
                with self.runtime.sessions() as db:
                    row, _ = self.runtime.workflows.load(db, actor, instance_id)
                    item = {"flow_id": row.flow_id, "revision": row.revision}
            host_id, host_kind = item["flow_id"], "workflow_step"
        else:
            if business_read:
                item = self.runtime.interactions.get(actor, instance_id)
            else:
                with self.runtime.sessions() as db:
                    row, _ = self.runtime.interactions.load(db, actor, instance_id)
                    item = {"component_id": row.component_id, "revision": row.revision}
            host_id, host_kind = item["component_id"], "interaction"
        for source in self.runtime.module_catalog.sources():
            candidates = [
                a
                for a in source["graph"]["atoms"]
                if a["kind"] == "frontend"
                and a["host"]["id"] == host_id
                and a["host"]["kind"] == host_kind
            ]
            if candidates:
                # load already checked this exact owned host's current access.
                # Validate its registered graph, but a heartbeat must not walk
                # every public business entry and availability in the module.
                module = (self.runtime.module_catalog.module(actor, source["module_id"])
                          if business_read else self.runtime.module_catalog.checked(source))
                atoms = [
                    a
                    for a in module["graph"]["atoms"]
                    if a["kind"] == "frontend"
                    and a["host"]["id"] == host_id
                    and a["host"]["kind"] == host_kind
                ]
                return item, module, atoms
        return item, None, []

    async def report(self, actor, kind, instance_id, body):
        """Accept bounded presentation status only after checking the real host."""
        item, module, atoms = await self.resolve(actor, kind, instance_id, business_read=False)
        if module is None:
            return {"registered": False}
        atom = next((a for a in atoms if a["host"].get("step_id") == body.step_id), None)
        require(atom is not None, "atomic_host", "当前宿主没有该原子组件。", 422)
        require(body.revision == item["revision"], "revision_conflict", "组件实例已经更新。", 409)
        now = self.runtime.observations.clock()
        with self.runtime.observations.database() as db:
            # Presence is short-lived. Pruning also bounds abandoned browser tabs.
            db.execute("DELETE FROM ui_presence WHERE received < ?", (now - 120,))
            count = db.execute(
                "SELECT COUNT(*) FROM ui_presence WHERE owner=?", (actor["id"],)
            ).fetchone()[0]
            require(
                count < 256
                or db.execute(
                    "SELECT 1 FROM ui_presence WHERE owner=? AND kind=? AND instance_id=? AND atom_id=? AND session_id=?",
                    (actor["id"], kind, instance_id, atom["id"], body.session_id),
                ).fetchone(),
                "atomic_presence_limit",
                "当前组件观察数量已达到上限。",
                429,
            )
            db.execute(
                "INSERT INTO ui_presence VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,kind,instance_id,atom_id,session_id) DO UPDATE SET surface=excluded.surface,status=excluded.status,revision=excluded.revision,received=excluded.received",
                (
                    actor["id"],
                    kind,
                    instance_id,
                    atom["id"],
                    body.session_id,
                    body.surface,
                    body.status,
                    body.revision,
                    now,
                ),
            )
        return {"registered": True, "atom_id": atom["id"]}

    async def read(self, actor, kind, instance_id):
        """Separate saved drafts, module projections and transient browser reports."""
        item, module, atoms = await self.resolve(actor, kind, instance_id)
        with self.runtime.observations.database() as db:
            rows = db.execute(
                "SELECT * FROM ui_presence WHERE owner=? AND kind=? AND instance_id=?",
                (actor["id"], kind, instance_id),
            ).fetchall()
        now = self.runtime.observations.clock()
        instances = []
        for atom in atoms:
            step = atom["host"].get("step_id")
            presence = [
                {
                    "session_id": r["session_id"],
                    "surface": r["surface"],
                    "status": r["status"],
                    "fresh": now - r["received"] <= 15 and r["revision"] == item["revision"],
                    "received_at": r["received"],
                }
                for r in rows
                if r["atom_id"] == atom["id"]
            ]
            instances.append(
                {
                    "atom_id": atom["id"],
                    "instance_id": instance_id + (":" + step if step else ""),
                    "host": atom["host"],
                    "nodes": [m for m in module["graph"]["mappings"] if m["atom_id"] == atom["id"]],
                    "local_state": (
                        item["drafts"].get(step, {}) if kind == "workflow" else item["state"]
                    ),
                    "presence": presence,
                }
            )
        projection = item.get("projection")
        return {
            "registered": module is not None,
            "host_instance_id": instance_id,
            "revision": item["revision"],
            "instances": instances,
            "business_state": (
                {key: projection[key] for key in ("object_ref", "state", "revision")}
                if projection
                else None
            ),
            "cursor": item.get("cursor"),
            "execution_status": item["status"],
            "run_id": item.get("run_id"),
        }


def create_atomic_router(service, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1/atomic-instances")

    @router.get("/{kind}/{instance_id}")
    async def read(kind: str, instance_id: str, actor=Depends(require_user)):
        return await service.read(actor, kind, instance_id)

    @router.post("/{kind}/{instance_id}/presence")
    async def report(kind: str, instance_id: str, body: PresenceInput, actor=Depends(check_csrf)):
        return await service.report(actor, kind, instance_id, body)

    return router
