"""Authenticated conversation history, branch and owner-scoped UI state API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageInput(StrictModel):
    content: str = Field(min_length=1, max_length=24000)
    model_profile_id: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ForkInput(StrictModel):
    message_id: str | None = Field(default=None, max_length=40)


class RenameInput(StrictModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationDraft(StrictModel):
    text: str = Field(default="", max_length=24000)
    model_profile_id: str = Field(default="", max_length=120)
    active_capability_id: str | None = Field(default=None, max_length=160)
    form_inputs: dict[str, dict] = Field(default_factory=dict, max_length=30)


class StateInput(StrictModel):
    expected_revision: int = Field(ge=1)
    current_conversation_id: str | None = Field(default=None, max_length=40)
    drafts: dict[str, ConversationDraft | None] = Field(default_factory=dict, max_length=20)


def create_conversation_router(service, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1")

    @router.post("/conversations")
    def create(actor=Depends(check_csrf)):
        return service.create(actor)

    @router.get("/conversations")
    def list_conversations(actor=Depends(require_user)):
        return service.list(actor)

    # Static state routes must precede the conversation-id route.
    @router.get("/conversations/state")
    def read_state(actor=Depends(require_user)):
        return service.state(actor)

    @router.patch("/conversations/state")
    def write_state(body: StateInput, actor=Depends(check_csrf)):
        payload = body.model_dump()
        if "current_conversation_id" not in body.model_fields_set:
            payload.pop("current_conversation_id")
        return service.save_state(actor, payload)

    @router.get("/conversations/{conversation_id}")
    def get(conversation_id: str, actor=Depends(require_user)):
        return service.get(actor, conversation_id)

    @router.patch("/conversations/{conversation_id}")
    def rename(conversation_id: str, body: RenameInput, actor=Depends(check_csrf)):
        return service.rename(actor, conversation_id, body.title)

    @router.post("/conversations/{conversation_id}/messages")
    async def send(conversation_id: str, body: MessageInput, actor=Depends(check_csrf)):
        return await service.send(
            actor, conversation_id, body.content, body.model_profile_id, body.idempotency_key
        )

    @router.post("/conversations/{conversation_id}/fork")
    def fork(conversation_id: str, body: ForkInput, actor=Depends(check_csrf)):
        return service.fork(actor, conversation_id, body.message_id)

    return router
