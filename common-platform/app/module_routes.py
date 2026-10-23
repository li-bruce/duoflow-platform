from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field


class PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


def management_modules(runtime):
    """Describe loaded definitions, without claiming a user is authorized/ready."""
    result = runtime.modules()
    services = (
        {item["id"]: item for item in runtime.remote_services.list_services()["items"]}
        if runtime.remote_services
        else {}
    )
    policies = {item["module_id"]: item for item in runtime.access.definitions()}
    flows = runtime.workflows.definitions
    components = runtime.interactions.definitions
    for item in result["items"]:
        owned = {cap["id"] for cap in item["capabilities"]}
        manifest = services.get(item.get("service_id"), {}).get("manifest")
        if manifest:
            item["manifest_protocol_version"] = manifest["protocol_version"]
            item["invoke_protocol_version"] = (
                "1.1" if manifest["protocol_version"] == "1.2" else "1.0"
            )
        policy = policies.get(item["id"])
        flow_ids = sorted(
            key for key, flow in flows.items() if flow["provider"]["read_capability"] in owned
        )
        referenced = {entry["flow_id"] for entry in policy["experiences"]} if policy else set()
        item["registration"] = {
            "access_definition": (
                ("registered" if policy["enabled"] else "disabled") if policy else "not_registered"
            ),
            "flow_ids": flow_ids,
            "component_ids": sorted(
                key
                for key, component in components.items()
                if any(action["capability_id"] in owned for action in component["actions"])
            ),
            "missing_flow_ids": sorted(referenced - set(flow_ids)),
            "unmapped_capability_ids": (
                sorted(owned - {entry["capability_id"] for entry in policy["command_mappings"]})
                if policy
                else []
            ),
        }
    return result


def create_module_router(runtime, require_admin, require_user, check_csrf):
    router = APIRouter(tags=["modules"])

    @router.get("/modules")
    def modules(actor=Depends(require_admin)):
        return management_modules(runtime)

    @router.post("/modules/{module_id}/enable", dependencies=[Depends(check_csrf)])
    async def enable(module_id: str, body: PublicationInput, actor=Depends(require_admin)):
        return await runtime.set_module_publication(
            module_id, True, body.expected_revision, actor=actor
        )

    @router.post("/modules/{module_id}/disable", dependencies=[Depends(check_csrf)])
    async def disable(module_id: str, body: PublicationInput, actor=Depends(require_admin)):
        return await runtime.set_module_publication(
            module_id, False, body.expected_revision, actor=actor
        )

    @router.get("/module-pages/{module_id}/{tool_id}")
    def page(module_id: str, tool_id: str, request: Request, actor=Depends(require_user)):
        return runtime.module_page(module_id, tool_id, actor, str(request.base_url).rstrip("/"))

    return router
