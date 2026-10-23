from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Strict):
    component_id: str = Field(min_length=3, max_length=120)
    input: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=40)


class Edit(Strict):
    state: dict
    expected_revision: int = Field(ge=1)


class Action(Strict):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=100)


def create_interaction_router(service, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1/interactions")

    @router.get("/catalog")
    def catalog(actor=Depends(require_user)):
        return service.catalog(actor)

    @router.get("/instances")
    def instances(actor=Depends(require_user)):
        return service.list(actor)

    @router.post("/instances")
    def create(body: Create, actor=Depends(check_csrf)):
        return service.create(
            actor, body.component_id, body.input, body.idempotency_key, body.conversation_id
        )

    @router.get("/instances/{instance_id}")
    def get(instance_id: str, actor=Depends(require_user)):
        return service.get(actor, instance_id)

    @router.patch("/instances/{instance_id}")
    def patch(instance_id: str, body: Edit, actor=Depends(check_csrf)):
        return service.patch(actor, instance_id, body.state, body.expected_revision)

    @router.post("/instances/{instance_id}/actions/{action_id}")
    async def action(instance_id: str, action_id: str, body: Action, actor=Depends(check_csrf)):
        return await service.action(
            actor, instance_id, action_id, body.expected_revision, body.idempotency_key
        )

    @router.post("/instances/{instance_id}/attach")
    def attach(instance_id: str, actor=Depends(check_csrf)):
        return service.attach(actor, instance_id)

    return router
