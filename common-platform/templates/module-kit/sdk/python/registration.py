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
    def __init__(
        self,
        manifest,
        document,
        handlers,
        workflow_handlers=None,
        *,
        graph=None,
        runtime_probe=None
    ):
        """Validate startup bindings and pin complete local declarations."""
        workflow_handlers = workflow_handlers or {}
        self._need(
            document.get("registration_version") in {"1.0", "1.1"}
            and manifest["protocol_version"] in ("1.0", "1.1", "1.2")
        )
        self._need(
            (document["module_id"], document["module_version"])
            == (manifest["module"]["id"], manifest["module"]["version"])
        )
        self.ids = sorted(cap["id"] for cap in manifest["capabilities"])
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
