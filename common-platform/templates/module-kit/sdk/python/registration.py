"""Portable bilateral registration v1 for an existing Python HTTP adapter.

The adapter authenticates the connection before calling confirm. Handlers must
be the actual dispatch map, not a second list of claimed function names. This
module performs no business calls, database access or user authorization grants.
"""

from copy import deepcopy
import re
from types import MappingProxyType

from jsonschema import Draft202012Validator


class RegistrationError(ValueError):
    """Map to HTTP 422 with fixed wording, never echo a received contract."""


class Registration:
    @classmethod
    def _validate_coordination(cls, document):
        """Portable startup checks; no dependency on a platform installation."""
        if "coordination" not in document:
            return
        declaration = document["coordination"]
        kinds = {"state.changed", "publication.requested", "resource.published", "resource.withdrawn"}

        def keys(value, expected):
            return isinstance(value, dict) and set(value) == set(expected)

        def identifier(value):
            return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{2,119}", value)

        def local_identifier(value):
            return isinstance(value, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,159}", value)

        def state_identifier(value):
            return isinstance(value, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,127}", value)

        def bounded(value, minimum, maximum, check):
            return (isinstance(value, list) and minimum <= len(value) <= maximum
                    and all(check(item) for item in value) and len(set(value)) == len(value))

        cls._need(keys(declaration, {"version", "machines", "emits", "accepts"})
                  and declaration["version"] == "1.0"
                  and isinstance(declaration["machines"], list) and len(declaration["machines"]) <= 32
                  and bounded(declaration["emits"], 0, 4, lambda value: isinstance(value, str) and value in kinds)
                  and isinstance(declaration["accepts"], list) and len(declaration["accepts"]) <= 128)
        prefix = document["module_id"] + "."
        objects = {obj["id"] for obj in document["data_objects"]}
        graph_machines = {machine["id"]: machine for machine in
                          document.get("module_graph", {}).get("state_machines", [])
                          if machine["scope"] == "business"}
        machine_ids = set()
        for machine in declaration["machines"]:
            cls._need(keys(machine, {"id", "object_types", "states", "public_states", "approval_required"})
                      and local_identifier(machine["id"]) and machine["id"].startswith(prefix)
                      and machine["id"] not in machine_ids
                      and bounded(machine["object_types"], 1, 32,
                                  lambda value: identifier(value) and value.startswith(prefix) and value in objects)
                      and bounded(machine["states"], 1, 100, state_identifier)
                      and bounded(machine["public_states"], 0, 100, state_identifier)
                      and set(machine["public_states"]).issubset(machine["states"])
                      and isinstance(machine["approval_required"], bool))
            if document["registration_version"] == "1.1":
                actual = graph_machines.get(machine["id"])
                cls._need(actual is not None and set(machine["states"]) == {state["id"] for state in actual["states"]})
            machine_ids.add(machine["id"])
        accepts = set()
        for item in declaration["accepts"]:
            cls._need(keys(item, {"source_module", "kind", "mode"}) and identifier(item["source_module"])
                      and item["source_module"] != document["module_id"]
                      and isinstance(item["kind"], str) and item["kind"] in kinds and item["kind"] != "publication.requested"
                      and isinstance(item["mode"], str) and item["mode"] in {"notify", "sync"})
            identity = (item["source_module"], item["kind"])
            cls._need(identity not in accepts)
            accepts.add(identity)

    def __init__(
        self,
        manifest,
        document,
        handlers,
        workflow_handlers=None,
        *,
        graph=None,
        runtime_probe=None,
        required_platform_features=(),
        ai_manager=None,
    ):
        """Validate startup bindings and pin complete local declarations."""
        workflow_handlers = workflow_handlers or {}
        self._need(
            isinstance(required_platform_features, (list, tuple))
            and len(required_platform_features) <= 20
            and all(isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9.-]{0,79}", item)
                    for item in required_platform_features)
            and len(set(required_platform_features)) == len(required_platform_features)
        )
        self.required_platform_features = tuple(required_platform_features)
        self._need(
            document.get("registration_version") in {"1.0", "1.1"}
            and manifest["protocol_version"] in ("1.0", "1.1", "1.2")
        )
        self._need(
            (document["module_id"], document["module_version"])
            == (manifest["module"]["id"], manifest["module"]["version"])
        )
        self.ids = sorted(cap["id"] for cap in manifest["capabilities"])
        self._validate_coordination(document)
        if "ai_services" in document:
            services = document["ai_services"]
            self._need(isinstance(services, list) and len(services) <= 16
                       and (not services or document["permissions"]["mode"] == "platform_access"))
            ai_ids = set()
            for service in services:
                self._need(
                    isinstance(service, dict)
                    and set(service) == {"id", "title", "description", "capability_ids", "supported_sources"}
                    and isinstance(service["id"], str)
                    and re.fullmatch(r"[a-z][a-z0-9_.-]{2,119}", service["id"])
                    and service["id"].startswith(manifest["module"]["id"] + ".")
                    and service["id"] not in ai_ids
                    and isinstance(service["title"], str) and 0 < len(service["title"]) <= 200
                    and isinstance(service["description"], str) and 0 < len(service["description"]) <= 2000
                    and isinstance(service["capability_ids"], list) and 0 < len(service["capability_ids"]) <= 64
                    and all(item in self.ids for item in service["capability_ids"])
                    and len(set(service["capability_ids"])) == len(service["capability_ids"])
                    and isinstance(service["supported_sources"], list) and service["supported_sources"]
                    and all(item in {"platform", "module"} for item in service["supported_sources"])
                    and len(set(service["supported_sources"])) == len(service["supported_sources"])
                )
                ai_ids.add(service["id"])
        self.ai_manager = ai_manager
        self._need(not document.get("ai_services") or ai_manager is not None
                   and ai_manager.services == document["ai_services"])
        self.modern = document["registration_version"] == "1.1"
        self.runtime_probe = runtime_probe
        if self.modern:
            from .atoms import AtomBindings

            self._need(graph == document["module_graph"] and callable(runtime_probe))
            AtomBindings(graph, manifest, handlers)
        self._need(sorted(handlers) == self.ids and all(callable(fn) for fn in handlers.values()))
        self._need(
            sorted(workflow_handlers) == document["workflow_operations"]
            and all(callable(fn) for fn in workflow_handlers.values())
        )
        for cap in manifest["capabilities"]:
            for field in ("input_schema", "output_schema"):
                Draft202012Validator.check_schema(cap[field])
        self._manifest, self._document = deepcopy(manifest), deepcopy(document)
        self.handlers = MappingProxyType(dict(handlers))
        self.workflow_handlers = MappingProxyType(dict(workflow_handlers))

    @property
    def manifest(self):
        """Expose a copy, keeping the contract checked at startup immutable."""
        return deepcopy(self._manifest)

    @property
    def document(self):
        """Return the local public declaration without exposing mutable storage."""
        return deepcopy(self._document)

    @staticmethod
    def _need(condition):
        if not condition:
            raise RegistrationError("注册确认未通过，请检查协议、声明与接口绑定。")

    def confirm(self, offer):
        """Actively compare the platform offer, then acknowledge its unique nonce."""
        self._need(not self.document.get("ai_services")
                   or self.ai_manager.platform_connected is True)
        self._need(
            isinstance(offer, dict)
            and set(offer)
            == (
                {
                    "registration_version",
                    "nonce",
                    "contract_digest",
                    "platform_features",
                    "manifest",
                    "registration",
                }
                | ({"runtime_probe"} if self.modern else set())
            )
        )
        self._need(offer["registration_version"] == self.document["registration_version"])
        for key, length in (("nonce", 32), ("contract_digest", 64)):
            self._need(
                isinstance(offer[key], str) and re.fullmatch("[0-9a-f]{%d}" % length, offer[key])
            )
        required = {
            "invoke-1.1" if self.manifest["protocol_version"] == "1.2" else "invoke-1.0",
            "shared-hosts-1.0",
            "registration-1.0",
        }
        required.update(self.required_platform_features)
        if self.document.get("ai_services"):
            required.add("ai-services-1.0")
        if "coordination" in self.document:
            required.add("coordination-1.0")
        if self.document["permissions"]["mode"] == "platform_access":
            required.add("access-1.0")
        if self.modern:
            required.update(
                {
                    "registration-1.1",
                    "module-graph-1.0",
                    "flow-exports-1.0",
                    "runtime-observation-1.0",
                }
            )
        self._need(
            isinstance(offer["platform_features"], list)
            and all(isinstance(value, str) for value in offer["platform_features"])
        )
        self._need(required.issubset(offer["platform_features"]))
        self._need(offer["manifest"] == self.manifest and offer["registration"] == self.document)
        if self.modern:
            self._need(self.runtime_probe(offer["runtime_probe"]) is True)
        return {
            "registration_version": self.document["registration_version"],
            "nonce": offer["nonce"],
            "contract_digest": offer["contract_digest"],
            "accepted": True,
            "checks": {
                "platform_contract": True,
                "local_manifest": True,
                "local_registration": True,
                "handlers": True,
                "schemas": True,
                **({"atomic_bindings": True, "runtime_callback": True} if self.modern else {}),
            },
            "capability_ids": self.ids,
            "workflow_operations": self.document["workflow_operations"],
        }
