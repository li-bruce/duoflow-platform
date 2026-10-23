"""Central policy administration. Installation declares; only managers grant."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .db import User
from .errors import require
from .identity import has_permission, refresh_actor


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str = Field(min_length=1, max_length=40)
    module_id: str = Field(min_length=1, max_length=120)
    role_id: str = Field(min_length=1, max_length=160)
    scope: dict
    expires_at: float | None = None
    expected_policy_revision: int | None = None


class DefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: dict
    expected_revision: int | None = None


def create_access_router(runtime, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1/access")

    def manager(actor):
        with runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
        require(
            has_permission(actor, "identity.manage"),
            "forbidden",
            "管理业务授权需要身份管理权限。",
            403,
        )
        return actor

    @router.get("/definitions")
    def definitions(actor=Depends(require_user)):
        manager(actor)
        return {"items": runtime.access.definitions()}

    @router.post("/definitions")
    async def register(body: DefinitionInput, actor=Depends(check_csrf)):
        async with runtime.lock:
            actor = manager(actor)
            module_id = body.definition.get("module_id")
            owned = {
                s["id"]
                for s in runtime.local_registry.capabilities.values()
                if s["module_id"] == module_id
            }
            if not owned and runtime.remote_services:
                for service in runtime.remote_services.list_services()["items"]:
                    binding = runtime.remote_services.get_binding(service["id"])
                    manifest = binding.get("manifest")
                    if manifest and manifest["module"]["id"] == module_id:
                        require(
                            manifest["protocol_version"] == "1.2",
                            "access_protocol",
                            "远程范围授权需要 manifest 1.2 与票据适配器。",
                            422,
                        )
                        owned = {c["id"] for c in manifest["capabilities"]}
                        break
            require(bool(owned), "access_module", "先安装或发现对应模块，再注册其权限定义。", 422)
            # The schema validator runs before inspecting mapping structures.
            from .access_control import validate_definition

            validate_definition(body.definition)
            require(
                all(m["capability_id"] in owned for m in body.definition["command_mappings"]),
                "access_mapping",
                "权限映射只能引用对应模块的已发现能力。",
                422,
            )
            for experience in body.definition["experiences"]:
                definition = runtime.workflows.definitions.get(experience["flow_id"])
                require(
                    definition is not None and definition["provider"]["read_capability"] in owned,
                    "access_experience",
                    "办理视图必须引用本模块已安装的流程。",
                    422,
                )
            return runtime.access.register_definition(
                body.definition, actor_id=actor["id"], expected_revision=body.expected_revision
            )

    @router.get("/grants")
    def grants(subject_id: str, actor=Depends(require_user)):
        manager(actor)
        return {"items": runtime.access.bindings(subject_id)}

    @router.get("/mine")
    def mine(actor=Depends(require_user)):
        with runtime.sessions() as db:
            actor = refresh_actor(db, actor)
        return {
            "items": runtime.access.bindings(actor["id"]),
            "experiences": runtime.access.experiences(actor),
        }

    @router.post("/grants")
    async def grant(body: GrantInput, actor=Depends(check_csrf)):
        async with runtime.lock:
            actor = manager(actor)
            with runtime.sessions() as db:
                user = db.get(User, body.subject_id)
                require(
                    user is not None and user.enabled,
                    "access_subject",
                    "授权对象必须是有效账号。",
                    422,
                )
            require(
                any(m["id"] == body.module_id for m in runtime.registry.modules),
                "access_module",
                "模块未启用。",
                409,
            )
            return runtime.access.grant(**body.model_dump(), actor_id=actor["id"])

    @router.post("/grants/{grant_id}/revoke")
    async def revoke(grant_id: str, actor=Depends(check_csrf)):
        async with runtime.lock:
            actor = manager(actor)
            return runtime.access.revoke(grant_id, actor_id=actor["id"])

    return router
