from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Strict):
    flow_id: str = Field(min_length=3, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=40)


class Edit(Strict):
    cursor: str = Field(min_length=1, max_length=80)
    drafts: dict
    expected_revision: int = Field(ge=1)


class Select(Strict):
    object_id: str = Field(max_length=100, pattern=r"^([A-Za-z0-9][A-Za-z0-9_-]*)?$")
    expected_revision: int = Field(ge=1)


class Action(Strict):
    command: dict = Field(default_factory=dict)
    expected_revision: int = Field(ge=1)
    expected_object_revision: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=100)


class Page(Strict):
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class ObjectList(Page):
    object_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")


class Detail(Page):
    section: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")


def create_workflow_router(service, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1/flows")

    @router.get("/catalog")
    def catalog(actor=Depends(require_user)):
        return service.catalog(actor)

    @router.get("/instances")
    def instances(actor=Depends(require_user)):
        return service.list(actor)

    @router.post("/instances")
    async def create(body: Create, actor=Depends(check_csrf)):
        return await service.create(actor, body.flow_id, body.idempotency_key, body.conversation_id)

    @router.get("/instances/{instance_id}")
    async def get(instance_id: str, actor=Depends(require_user)):
        return await service.get(actor, instance_id)

    @router.post("/instances/{instance_id}/objects/list")
    async def object_list(instance_id: str, body: ObjectList, actor=Depends(check_csrf)):
        return await service.object_list(
            actor, instance_id, body.object_type, body.cursor, body.limit
        )

    @router.post("/instances/{instance_id}/detail")
    async def detail(instance_id: str, body: Detail, actor=Depends(check_csrf)):
        return await service.detail(actor, instance_id, body.section, body.cursor, body.limit)

    @router.patch("/instances/{instance_id}")
    async def patch(instance_id: str, body: Edit, actor=Depends(check_csrf)):
        return await service.patch(
            actor, instance_id, body.cursor, body.drafts, body.expected_revision
        )

    @router.post("/instances/{instance_id}/select")
    async def select(instance_id: str, body: Select, actor=Depends(check_csrf)):
        return await service.select(actor, instance_id, body.object_id, body.expected_revision)

    @router.post("/instances/{instance_id}/actions/{action_id}")
    async def action(instance_id: str, action_id: str, body: Action, actor=Depends(check_csrf)):
        return await service.action(
            actor,
            instance_id,
            action_id,
            body.command,
            body.expected_revision,
            body.expected_object_revision,
            body.idempotency_key,
        )

    @router.post("/instances/{instance_id}/attach")
    def attach(instance_id: str, actor=Depends(check_csrf)):
        return service.attach(actor, instance_id)

    return router
