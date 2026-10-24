"""Read installed module contracts and expose authorized, versioned public entries."""

from copy import deepcopy
import json
from pydantic import BaseModel, ConfigDict, Field

from .errors import PlatformError, require
from .flow_exports import resolved_flows
from .module_graph import validate_graph_hosts
from .registration_contract import digest
from .registry import inside


class OpenBusiness(BaseModel):
    """Opening an entry creates a personal host, never a business command."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=30)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ModuleCatalog:
    def __init__(self, runtime):
        self.runtime = runtime

    def sources(self):
        """Use trusted installed files or an admitted remote registration snapshot."""
        local = self.runtime.root / "extensions"
        installed = local / "installed.json"
        if installed.exists():
            for folder in json.loads(installed.read_text(encoding="utf-8-sig"))["modules"]:
                base = inside(local, folder)
                if not (base / "module-graph.json").exists():
                    continue
                read = lambda name: json.loads((base / name).read_text(encoding="utf-8-sig"))
                manifest = read("manifest.json")
                manifest["capabilities"] = [
                    c
                    for c in self.runtime.local_registry.capabilities.values()
                    if c["module_id"] == manifest["id"]
                ]
                registration = (
                    read("registration.json")
                    if (base / "registration.json").exists()
                    else {
                        "permissions": {
                            "mode": "platform_access",
                            "definition": read("access-definition.json"),
                        },
                        "data_objects": [],
                    }
                )
                yield {
                    "module_id": manifest["id"],
                    "source": "local",
                    "service_id": None,
                    "binding_revision": None,
                    "manifest": manifest,
                    "registration": registration,
                    "graph": read("module-graph.json"),
                    "exports": read("flow-exports.json"),
                }
        if self.runtime.remote_services:
            for service in self.runtime.remote_services.list_services()["items"]:
                registration = service.get("registration") or {}
                if not service["enabled"] or registration.get("registration_version") != "1.1":
                    continue
                try:
                    self.runtime.remote_services.require_admitted(service)
                except PlatformError:
                    # A stale service must not hide other usable modules.
                    continue
                yield {
                    "module_id": registration["module_id"],
                    "source": "remote",
                    "service_id": service["id"],
                    "binding_revision": service["revision"],
                    "manifest": service["manifest"],
                    "registration": registration,
                    "graph": registration["module_graph"],
                    "exports": registration["flow_exports"],
                }

    def checked(self, source):
        """Validate against today's actual shared components before exposing a graph."""
        capabilities = {c["id"] for c in source["manifest"]["capabilities"]}
        workflows = [
            w
            for w in self.runtime.workflows.definitions.values()
            if w["provider"]["read_capability"] in capabilities
        ]
        interactions = [
            i
            for i in self.runtime.interactions.definitions.values()
            if any(a["capability_id"] in capabilities for a in i["actions"])
        ]
        graph = validate_graph_hosts(
            source["graph"], source["manifest"], interactions=interactions, workflows=workflows
        )
        flows = resolved_flows(
            source["exports"],
            source["manifest"],
            source["registration"],
            graph,
            workflows=workflows,
        )
        return {
            **source,
            "graph": graph,
            "flows": flows,
            "contract_digest": digest(
                {
                    "manifest": source["manifest"],
                    "registration": source["registration"],
                    "graph": graph,
                    "exports": source["exports"],
                }
            ),
        }

    def module(self, actor, module_id, *, require_available=True):
        """Resolve authorized entries, optionally including temporarily offline services.

        Saved compositions still need their accepted contract during an outage.
        Disabling publication or changing permissions always removes access.
        """
        source = next((s for s in self.sources() if s["module_id"] == module_id), None)
        require(source is not None, "module_missing", "模块尚未登记公开业务。", 404)
        value = self.checked(source)
        visible = []
        for flow in value["flows"]:
            start = (
                flow["entry"]["id"]
                if flow["entry"]["kind"] == "capability"
                else self.runtime.workflows.definitions[flow["entry"]["id"]]["provider"][
                    "read_capability"
                ]
            )
            try:
                spec = self.runtime.registry.get(start, actor)
                if not require_available or self.runtime.availability(spec)["available"]:
                    visible.append(flow)
            except PlatformError:
                continue
        require(visible, "forbidden", "当前没有可使用的模块业务。", 403)
        return {**value, "flows": visible}

    def flow(self, actor, module_id, flow_id, version, *, require_available=True):
        """Resolve one public entry without granting later action or object access."""
        module = self.module(actor, module_id, require_available=require_available)
        flow = next(
            (f for f in module["flows"] if f["id"] == flow_id and f["version"] == version), None
        )
        require(
            flow is not None, "flow_export_missing", "公开业务不存在、版本变化或当前无权进入。", 403
        )
        return module, deepcopy(flow)

    def catalog(self, actor):
        """List usable modules without disclosing service credentials or user grants."""
        items = []
        for source in self.sources():
            try:
                module = self.module(actor, source["module_id"])
            except PlatformError:
                continue
            metadata = module["manifest"].get("module", module["manifest"])
            items.append(
                {
                    "module_id": module["module_id"],
                    "title": metadata["name"],
                    "description": metadata["description"],
                    "version": metadata["version"],
                    "source": module["source"],
                    "service_id": module["service_id"],
                    "contract_digest": module["contract_digest"],
                    "flows": module["flows"],
                }
            )
        return {"items": items}

    def guidance(self, actor, text):
        """Guide a named public business entry into the existing shared host.

        This prepares a component or form. It does not execute a business action
        while interpreting text, so the normal confirmation and guards remain.
        """
        explicit = (
            text.removeprefix("/business ").strip() if text.startswith("/business ") else None
        )
        matches = [
            (m, f)
            for m in self.catalog(actor)["items"]
            for f in m["flows"]
            if f["id"] == explicit or text in {f["title"], "打开" + f["title"]}
        ]
        if not matches:
            return (
                {"content": "此业务未登记或当前身份无法进入。", "views": []} if explicit else None
            )
        if len(matches) > 1:
            return {
                "content": "找到多个同名业务，请选择明确的业务编号：\n"
                + "\n".join(f["id"] for _, f in matches),
                "views": [],
            }
        module, flow = matches[0]
        source = self.module(actor, module["module_id"])
        return {"content": flow["description"], "views": [self.entry_view(source, flow)]}

    def entry_view(self, module, flow):
        """Choose the same component for the module page and guided conversation."""
        entry = flow["entry"]
        if entry["kind"] == "workflow":
            view = {"type": "workflow-request", "data": {"flow_id": entry["id"]}}
        else:
            components = [
                a["host"]["id"]
                for a in module["graph"]["atoms"]
                if a["kind"] == "frontend"
                and a["host"]["kind"] == "interaction"
                and any(e.get("capability", {}).get("id") == entry["id"] for e in a["events"])
                and not self.runtime.interactions.definitions[a["host"]["id"]]["input_schema"].get(
                    "required"
                )
            ]
            view = (
                {
                    "type": "interaction-request",
                    "data": {"component_id": components[0], "initial_input": {}},
                }
                if len(components) == 1
                else {"type": "form", "data": {"capability_id": entry["id"], "input": {}}}
            )
        return view

    async def open(self, actor, module_id, flow_id, body):
        """Recheck the live contract before opening an owned, recoverable component."""
        module, flow = self.flow(actor, module_id, flow_id, body.version)
        view = self.entry_view(module, flow)
        if view["type"] == "workflow-request":
            item = await self.runtime.workflows.create(
                actor, view["data"]["flow_id"], body.idempotency_key
            )
            view = {"type": "workflow", "data": {"instance_id": item["id"]}}
        elif view["type"] == "interaction-request":
            item = self.runtime.interactions.create(
                actor, view["data"]["component_id"], {}, body.idempotency_key
            )
            view = {"type": "interaction", "data": {"instance_id": item["id"]}}
        return {"module_id": module_id, "flow_id": flow_id, "view": view}


def create_module_catalog_router(catalog, require_user, check_csrf):
    """Expose authorized module boundaries for the page and conversation selectors."""
    from fastapi import APIRouter, Depends

    router = APIRouter(prefix="/api/v1/business-modules")

    @router.get("")
    def list_modules(actor=Depends(require_user)):
        return catalog.catalog(actor)

    @router.get("/{module_id}")
    def module(module_id: str, actor=Depends(require_user)):
        value = catalog.module(actor, module_id)
        return {
            "module_id": module_id,
            "contract_digest": value["contract_digest"],
            "graph": value["graph"],
            "flows": value["flows"],
        }

    @router.post("/{module_id}/flows/{flow_id}/open")
    async def open_entry(
        module_id: str, flow_id: str, body: OpenBusiness, actor=Depends(check_csrf)
    ):
        return await catalog.open(actor, module_id, flow_id, body)

    return router
