"""Authenticated composition definitions and owned execution references."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Save(Strict):
    document: dict


class Start(Strict):
    definition_id: str = Field(min_length=1, max_length=40)
    input: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=40)


class Advance(Strict):
    expected_revision: int = Field(ge=1)
    confirm_step: str | None = Field(default=None, max_length=80)
    reconcile: bool = False


class RoleGrant(Strict):
    subject_id: str = Field(min_length=1, max_length=40)
    role_id: str = Field(min_length=1, max_length=80)
    expires_at: float | None = None


class Publication(Strict):
    shared: bool


def create_composition_router(service, require_user, check_csrf):
    """Bind session/CSRF checks to the service; business decisions stay in that service."""
    router = APIRouter(prefix="/api/v1/compositions")

    @router.get("/definitions")
    def definitions(actor=Depends(require_user)):
        return service.definitions(actor)

    @router.post("/definitions")
    def save(body: Save, actor=Depends(check_csrf)):
        return service.save(actor, body.document)

    @router.get("/definitions/{definition_id}")
    def definition(definition_id: str, actor=Depends(require_user)):
        with service.runtime.sessions() as db:
            row = service.definition(db, actor, definition_id)
            service.compile(actor, row.document)
            return {"id": row.id, "document": row.document, "fingerprint": row.fingerprint}

    @router.post("/definitions/{definition_id}/publication")
    async def publish(definition_id: str, body: Publication, actor=Depends(check_csrf)):
        async with service.runtime.lock:
            return service.publish(actor, definition_id, body.shared)

    @router.post("/check")
    def check(body: Save, actor=Depends(check_csrf)):
        document, pins = service.compile(actor, body.document)
        return {
            "valid": True,
            "document": document,
            "steps": [
                {"id": key, "flow": pin["flow"], "contract_digest": pin["contract_digest"]}
                for key, pin in pins.items()
            ],
        }

    @router.get("/runs")
    def runs(actor=Depends(require_user)):
        return service.runs(actor)

    @router.post("/definitions/{definition_id}/roles/grant")
    async def grant_role(definition_id: str, body: RoleGrant, actor=Depends(check_csrf)):
        async with service.runtime.lock:
            return service.grant_role(
                actor, definition_id, body.role_id, body.subject_id, body.expires_at
            )

    @router.post("/runs")
    async def start(body: Start, actor=Depends(check_csrf)):
        run_id = service.start_run(
            actor, body.definition_id, body.input, body.idempotency_key, body.conversation_id
        )
        return await service.execution.advance(actor, run_id)

    @router.get("/runs/{run_id}")
    async def get(run_id: str, actor=Depends(require_user)):
        return await service.read(actor, run_id)

    @router.post("/runs/{run_id}/advance")
    async def advance(run_id: str, body: Advance, actor=Depends(check_csrf)):
        return await service.execution.advance(
            actor,
            run_id,
            revision=body.expected_revision,
            confirm_step=body.confirm_step,
            reconcile=body.reconcile,
        )

    @router.post("/runs/{run_id}/attach")
    def attach(run_id: str, actor=Depends(check_csrf)):
        return service.attach(actor, run_id)

    @router.get("/runs/{run_id}/graph")
    async def graph(run_id: str, actor=Depends(require_user)):
        return await service.graph.read(actor, run_id)

    return router
