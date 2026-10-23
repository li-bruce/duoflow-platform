"""Read-through business inbox: private continuations + authorized module objects.

The center is an index, not another business database or state machine. Every
source read and open goes through the current flow and module authorization.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .db import WorkflowInstance
from .errors import require
from .identity import refresh_actor


class BusinessCenter:
    def __init__(self, runtime):
        self.runtime = runtime
        self.flows = runtime.workflows

    def catalog(self, actor):
        flows = self.flows.catalog(actor)["items"]
        experiences = self.runtime.access.experiences(actor)
        by_flow = {entry["flow_id"]: entry for entry in experiences}
        return {"items": [{**value, "experience": by_flow.get(value["id"])} for value in flows]}

    def instances(self, actor):
        values = []
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            for item in (
                db.query(WorkflowInstance)
                .filter_by(owner_id=actor["id"])
                .order_by(WorkflowInstance.updated_at.desc())
                .yield_per(100)
            ):
                try:
                    item, definition = self.flows.load(db, actor, item.id)
                except Exception as exc:
                    from .errors import PlatformError

                    if isinstance(exc, PlatformError):
                        continue
                    raise
                values.append(
                    {
                        "id": item.id,
                        "flow_id": item.flow_id,
                        "title": definition["title"],
                        "cursor": item.cursor,
                        "object_ref": item.object_ref,
                        "conversation_id": item.conversation_id,
                        "forked_from": item.forked_from,
                        "updated_at": item.updated_at,
                        "step_title": self.flows.step(definition, item.cursor)["title"],
                    }
                )
                if len(values) == 100:
                    break
        return {"items": values, "limit": 100}

    async def objects(self, actor, flow_id, cursor=None, limit=20):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
        stamp = self.runtime.access.authorization_stamp(actor)
        definition = self.flows.definition(flow_id, actor)
        provider = definition["provider"]
        payload = {"object_type": provider["object_type"], "limit": limit}
        if cursor:
            payload["cursor"] = cursor
        page = await self.flows.transport.request(
            actor, provider["read_capability"], "objects.list", payload
        )
        self.flows.definition(flow_id, actor)
        require(
            stamp == self.runtime.access.authorization_stamp(actor),
            "authorization_changed",
            "读取期间业务授权已变化。",
            403,
        )
        # Data supplied by the module is already an authorized projection. Only
        # bounded metadata enters the inbox; full evidence stays in the module.
        items = []
        for obj in page["objects"]:
            actions = [
                action
                for step in definition["steps"]
                for action in self.flows.actions(step, actor, obj, compact=True)
                if action["available"]
                and not action.get("creates_object")
                and action.get("mode") != "draft_query"
            ]
            data = obj.get("data", {})
            items.append(
                {
                    "object_ref": obj["object_ref"],
                    "revision": obj["revision"],
                    "state": obj["state"],
                    "title": str(
                        data.get("title") or data.get("name") or obj["object_ref"]["object_id"]
                    )[:200],
                    "project_id": str(data.get("project_id", ""))[:100],
                    "actions": actions,
                    "flow_id": flow_id,
                }
            )
        return {"items": items, "next_cursor": page.get("next_cursor"), "flow_id": flow_id}

    async def open(
        self,
        actor,
        *,
        instance_id=None,
        flow_id=None,
        object_id=None,
        idempotency_key=None,
        surface="page",
    ):
        if instance_id:
            require(
                not flow_id and object_id is None,
                "business_open",
                "恢复时仅提供个人流程编号。",
                422,
            )
            item = await self.flows.get(actor, instance_id)
        else:
            require(
                bool(flow_id) and bool(idempotency_key),
                "business_open",
                "请选择办理入口并提供请求编号。",
                422,
            )
            definition = self.flows.definition(flow_id, actor)
            if object_id:
                # Check the business object BEFORE creating any private record.
                await self.flows.transport.request(
                    actor,
                    definition["provider"]["read_capability"],
                    "objects.read",
                    {
                        "object_ref": {
                            "object_type": definition["provider"]["object_type"],
                            "object_id": object_id,
                        }
                    },
                )
            item = await self.flows.create(actor, flow_id, idempotency_key)
            if object_id:
                desired = {
                    "object_type": definition["provider"]["object_type"],
                    "object_id": object_id,
                }
                require(
                    item["object_ref"] in (None, desired),
                    "idempotency_conflict",
                    "此打开请求已经用于另一业务对象。",
                    409,
                )
                if item["object_ref"] is None:
                    item = await self.flows.select(actor, item["id"], object_id, item["revision"])
        result = {"instance_id": item["id"], "flow_id": item["flow_id"], "surface": surface}
        if surface == "conversation":
            result.update(self.flows.attach(actor, item["id"]))
        return result


class OpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str | None = Field(default=None, min_length=1, max_length=40)
    flow_id: str | None = Field(default=None, min_length=1, max_length=120)
    object_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    surface: str = Field(default="page", pattern=r"^(page|conversation)$")


def create_business_router(runtime, require_user, check_csrf):
    service = BusinessCenter(runtime)
    router = APIRouter(prefix="/api/v1/business")

    @router.get("/catalog")
    def catalog(actor=Depends(require_user)):
        return service.catalog(actor)

    @router.get("/instances")
    def instances(actor=Depends(require_user)):
        return service.instances(actor)

    @router.get("/objects")
    async def objects(
        flow_id: str, cursor: str | None = None, limit: int = 20, actor=Depends(require_user)
    ):
        require(
            1 <= limit <= 50 and (cursor is None or len(cursor) <= 2048),
            "business_page",
            "分页参数无效。",
            422,
        )
        return await service.objects(actor, flow_id, cursor, limit)

    @router.post("/open")
    async def open_item(body: OpenInput, actor=Depends(check_csrf)):
        return await service.open(actor, **body.model_dump())

    return router
