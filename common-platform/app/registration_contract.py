"""Pure registration rules shared by offline checks and remote admission.

No connections, grants or business operations are created here. A declaration
describes public interfaces; only the runtime can approve installed host pieces.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

VERSION = "1.0"
FEATURES = ["invoke-1.0", "invoke-1.1", "access-1.0", "shared-hosts-1.0", "registration-1.0"]
FEATURES += ["registration-1.1", "module-graph-1.0", "flow-exports-1.0", "runtime-observation-1.0"]
FEATURES += ["source-reads-1.0"]
FEATURES += ["ai-services-1.0"]
CHECKS = [
    ("manifest", "能力声明与输入输出"),
    ("registration", "数据、流程与权限声明"),
    ("authentication", "接口身份验证"),
    ("health", "服务健康状态"),
    ("invoke", "调用接口拒绝无效请求"),
    ("module_confirmation", "模块主动核对协议与接口"),
    ("host", "平台权限与呈现依赖"),
]
ROOT = Path(__file__).resolve().parents[1]


def check_schema(schema):
    """Bound schema traversal and disallow all forms of external references."""
    visited = 0

    def walk(node, depth=0):
        nonlocal visited
        visited += 1
        if depth > 40 or visited > 20000:
            raise ValueError("registration_schema")
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in node and (
                    not isinstance(node[key], str) or not node[key].startswith("#")
                ):
                    raise ValueError("registration_schema")
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(schema)
    Draft202012Validator.check_schema(schema)


def digest(value):
    """Platform-local content identity; modules compare offered JSON structurally.

    The module echoes this opaque digest only after comparing the full documents,
    so no cross-language floating point serialization algorithm is assumed.
    """
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def validate_ai_services(document, manifest):
    """Optional AI declarations bind services to concrete, scoped capabilities."""
    services = document.get("ai_services", [])
    schema = json.loads(
        (ROOT / "contracts/ai/v1/declaration.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(services)
    ids = [service["id"] for service in services]
    if len(ids) != len(set(ids)):
        raise ValueError("registration_ai_duplicate")
    if services and document["permissions"]["mode"] != "platform_access":
        raise ValueError("registration_ai_permissions")
    capabilities = {capability["id"] for capability in manifest["capabilities"]}
    for service in services:
        if not service["id"].startswith(document["module_id"] + "."):
            raise ValueError("registration_ai_namespace")
        if not set(service["capability_ids"]).issubset(capabilities):
            raise ValueError("registration_ai_capability")


def validate_registration(document, manifest):
    """Reject unknown fields and inconsistent references before contacting handlers."""
    version = document.get("registration_version") if isinstance(document, dict) else None
    if version not in {"1.0", "1.1"}:
        raise ValueError("registration_version")
    folder = "v1.1" if version == "1.1" else "v1"
    schema = json.loads(
        (ROOT / f"contracts/registration/{folder}/definition.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(document)
    validate_ai_services(document, manifest)
    if document["workflow_operations"] != sorted(document["workflow_operations"]):
        raise ValueError("registration_interfaces")
    if (document["module_id"], document["module_version"]) != (
        manifest["module"]["id"],
        manifest["module"]["version"],
    ):
        raise ValueError("registration_identity")
    caps = {c["id"]: c for c in manifest["capabilities"]}
    if any(not c.startswith(document["module_id"] + ".") for c in caps):
        raise ValueError("registration_namespace")
    for collection in ("data_objects", "business_chains"):
        ids = [entry["id"] for entry in document[collection]]
        if len(ids) != len(set(ids)):
            raise ValueError("registration_duplicate")
    for obj in document["data_objects"]:
        check_schema(obj["schema"])
        if not obj["id"].startswith(document["module_id"] + "."):
            raise ValueError("registration_namespace")
        if not obj["operations"]:
            raise ValueError("registration_data_operations")
        for operation, cap_id in obj["operations"].items():
            if cap_id not in caps or caps[cap_id]["kind"] != (
                "query" if operation in ("list", "read") else "command"
            ):
                raise ValueError("registration_data_capability")
    for chain in document["business_chains"]:
        if (
            not chain["id"].startswith(document["module_id"] + ".")
            or chain["entry_capability"] != chain["steps"][0]
            or any(c not in caps for c in chain["steps"])
        ):
            raise ValueError("registration_chain")
    permissions = document["permissions"]
    if permissions["mode"] == "platform_access":
        if manifest["protocol_version"] != "1.2":
            raise ValueError("registration_protocol")
        from .access_control import validate_definition

        definition = permissions["definition"]
        validate_definition(definition)
        mapped = {m["capability_id"] for m in definition["command_mappings"]}
        if definition["module_id"] != document["module_id"] or mapped != set(caps):
            raise ValueError("registration_permissions")
        if any(
            not set(obj["scope_fields"]).issubset(definition["scope_keys"])
            for obj in document["data_objects"]
        ):
            raise ValueError("registration_scope")
        required_flows = {
            reference["id"] for reference in document["host_requirements"]["workflows"]
        }
        if any(
            experience["flow_id"] not in required_flows for experience in definition["experiences"]
        ):
            raise ValueError("registration_experience")
    elif (
        permissions["definition"] is not None
        or document["data_objects"]
        or document["workflow_operations"]
        or document["host_requirements"]["workflows"]
    ):
        raise ValueError("registration_permissions")
    if version == "1.1":
        from .module_graph import validate_module_graph
        from .flow_exports import validate_export_references

        if permissions["mode"] == "platform_access":
            mappings = permissions["definition"]["command_mappings"]
            if len(mappings) != len(caps) or any("command" in mapping for mapping in mappings):
                raise ValueError("registration_concrete_permissions")
        validate_module_graph(document["module_graph"], manifest)
        validate_export_references(
            document["flow_exports"], manifest, document, document["module_graph"]
        )
    return document


def make_offer(manifest, registration, nonce):
    """Pin both complete documents and platform support for the module to verify."""
    return {
        "registration_version": registration["registration_version"],
        "nonce": nonce,
        "contract_digest": digest({"manifest": manifest, "registration": registration}),
        "platform_features": FEATURES,
        "manifest": manifest,
        "registration": registration,
    }


def validate_confirmation(response, offer):
    """An acknowledgement must cover every actual handler and required endpoint."""
    modern = offer["registration_version"] == "1.1"
    folder = "v1.1" if modern else "v1"
    schema = json.loads(
        (ROOT / f"contracts/registration/{folder}/confirmation.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(response)
    expected = {
        "registration_version",
        "nonce",
        "contract_digest",
        "accepted",
        "checks",
        "capability_ids",
        "workflow_operations",
    }
    if not isinstance(response, dict) or set(response) != expected:
        raise ValueError("registration_confirmation")
    for field in ("registration_version", "nonce", "contract_digest"):
        if response[field] != offer[field]:
            raise ValueError("registration_confirmation")
    checks = {
        "platform_contract": True,
        "local_manifest": True,
        "local_registration": True,
        "handlers": True,
        "schemas": True,
    }
    if modern:
        checks.update(atomic_bindings=True, runtime_callback=True)
    if response["accepted"] is not True or response["checks"] != checks:
        raise ValueError("registration_confirmation")
    if not all(value is True for value in response["checks"].values()):
        raise ValueError("registration_confirmation")
    expected_ids = sorted(c["id"] for c in offer["manifest"]["capabilities"])
    if (
        response["capability_ids"] != expected_ids
        or response["workflow_operations"] != offer["registration"]["workflow_operations"]
    ):
        raise ValueError("registration_handlers")


def validate_receipt(report, item):
    """Validate persisted evidence as strictly as fresh network evidence."""
    if (
        not isinstance(report, dict)
        or set(report)
        != {"version", "revision", "passed", "checked_at", "contract_digest", "checks"}
        or report["version"] != VERSION
        or type(report["revision"]) is not int
        or report["revision"] != item["revision"]
        or type(report["passed"]) is not bool
        or type(report["checked_at"]) not in (int, float)
        or not math.isfinite(report["checked_at"])
        or not isinstance(report["checks"], list)
        or any(not isinstance(check, dict) for check in report["checks"])
        or [check.get("id") for check in report["checks"]] != [key for key, _ in CHECKS]
        or any(
            set(check) != {"id", "title", "passed", "code"}
            or type(check["passed"]) is not bool
            or check["title"] != title
            or not isinstance(check["code"], str)
            or len(check["code"]) > 80
            or (check["passed"] and check["code"] != "ok")
            for check, (_, title) in zip(report["checks"], CHECKS)
        )
        or report["passed"] != all(check["passed"] for check in report["checks"])
        or report["contract_digest"]
        != digest({"manifest": item.get("manifest"), "registration": item["registration"]})
    ):
        raise ValueError("registration_receipt")
    if report["passed"]:
        validate_registration(item["registration"], item["manifest"])
