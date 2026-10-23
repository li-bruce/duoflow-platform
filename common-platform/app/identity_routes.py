"""Administrator-only identity endpoints, using the existing session and CSRF boundary."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .identity import IdentityService


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_display_fields(cls, value, info: ValidationInfo):
        if info.field_name in {
            "name",
            "username",
            "kind",
            "role",
            "organization_id",
        } and isinstance(value, str):
            return value.strip()
        return value


class OrganizationInput(IdentityInput):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="other", pattern="^(research|contributor|museum|other)$")


class TeamInput(IdentityInput):
    name: str = Field(min_length=1, max_length=160)
    organization_id: str = Field(min_length=1, max_length=40)


class UserInput(IdentityInput):
    username: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=20)
    roles: list[str] = Field(min_length=1, max_length=8)
    organization_id: str | None = Field(default=None, max_length=40)
    team_ids: list[str] = Field(default_factory=list, max_length=100)
    password: str | None = Field(default=None, min_length=12, max_length=200)


class RevisionInput(IdentityInput):
    expected_revision: int = Field(ge=1)


class UserUpdateInput(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, min_length=1, max_length=20)
    roles: list[str] | None = Field(default=None, min_length=1, max_length=8)
    organization_id: str | None = Field(default=None, max_length=40)
    team_ids: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class PasswordInput(RevisionInput):
    password: str | None = Field(default=None, min_length=12, max_length=200)


def create_identity_router(sessions, require_user, check_csrf):
    """Return /identity routes; caller mounts once with prefix /api/v1."""
    service = IdentityService(sessions)
    router = APIRouter(prefix="/identity", tags=["identity"])

    @router.get("/roles")
    def roles(actor=Depends(require_user)):
        return service.catalog(actor)

    @router.get("/users")
    def users(actor=Depends(require_user)):
        return service.list_users(actor)

    @router.get("/organizations")
    def organizations(actor=Depends(require_user)):
        return service.list_organizations(actor)

    @router.get("/teams")
    def teams(actor=Depends(require_user)):
        return service.list_teams(actor)

    @router.post("/organizations")
    def create_organization(body: OrganizationInput, actor=Depends(check_csrf)):
        return service.create_organization(actor, body.model_dump())

    @router.post("/teams")
    def create_team(body: TeamInput, actor=Depends(check_csrf)):
        return service.create_team(actor, body.model_dump())

    @router.post("/users")
    def create_user(body: UserInput, actor=Depends(check_csrf)):
        return service.create_user(actor, body.model_dump())

    @router.patch("/users/{user_id}")
    def update_user(user_id: str, body: UserUpdateInput, actor=Depends(check_csrf)):
        payload = body.model_dump(exclude_unset=True)
        # None is meaningful only for clearing organization membership.
        from .errors import require

        require(
            all(value is not None or key == "organization_id" for key, value in payload.items()),
            "identity_fields",
            "只有所属机构可以清空。",
            422,
        )
        return service.update_user(actor, user_id, payload)

    @router.post("/users/{user_id}/disable")
    def disable_user(user_id: str, body: RevisionInput, actor=Depends(check_csrf)):
        return service.update_user(actor, user_id, {**body.model_dump(), "enabled": False})

    @router.post("/users/{user_id}/enable")
    def enable_user(user_id: str, body: RevisionInput, actor=Depends(check_csrf)):
        return service.update_user(actor, user_id, {**body.model_dump(), "enabled": True})

    @router.post("/users/{user_id}/reset-password")
    def reset_password(user_id: str, body: PasswordInput, actor=Depends(check_csrf)):
        return service.reset_password(actor, user_id, body.model_dump())

    return router
