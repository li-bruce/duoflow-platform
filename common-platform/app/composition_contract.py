"""Validate public flow composition and explicit, typed data connections.

The first contract supports ordered steps. Binding paths select declared fields;
they cannot run expressions, traverse arbitrary objects, or grant permissions.
"""

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .errors import PlatformError, require

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1] / "contracts/compositions/v1/definition.schema.json"
    ).read_text(encoding="utf-8")
)
FORBIDDEN_KEYS = {"__proto__", "prototype", "constructor"}
MISSING = object()


def validate_input_schema(schema, depth=0, *, patterns=frozenset()):
    """Allow only local, bounded field schemas; never retrieve external references."""
    allowed = {
        "type",
        "title",
        "description",
        "default",
        "examples",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
    }
    require(
        isinstance(schema, dict) and depth <= 12 and not set(schema) - allowed,
        "composition_schema",
        "组合输入需要使用明确的本地字段类型，复杂转换请使用模块接口。",
        422,
    )
    require(
        isinstance(schema.get("type"), str),
        "composition_schema",
        "组合输入字段需要明确的类型。",
        422,
    )
    # User-defined plans can reuse admitted patterns, but cannot install new
    # regular expressions in the platform's validation process.
    if "pattern" in schema:
        require(
            isinstance(schema["pattern"], str) and schema["pattern"] in patterns,
            "composition_schema",
            "字段格式应沿用已接入模块的约束。",
            422,
        )
    if "properties" in schema:
        require(
            isinstance(schema["properties"], dict)
            and not set(schema["properties"]) & FORBIDDEN_KEYS,
            "composition_schema",
            "组合输入字段名称无效。",
            422,
        )
        for value in schema["properties"].values():
            validate_input_schema(value, depth + 1, patterns=patterns)
    if "items" in schema:
        validate_input_schema(schema["items"], depth + 1, patterns=patterns)
    if isinstance(schema.get("additionalProperties"), dict):
        validate_input_schema(schema["additionalProperties"], depth + 1, patterns=patterns)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise PlatformError("composition_schema", "组合输入的字段约束无效。", 422) from None


def at_path(value, path, *, optional=False):
    """Read declared data; an omitted optional field stays omitted, never becomes null."""
    for key in path:
        require(
            isinstance(value, dict) and key not in FORBIDDEN_KEYS,
            "composition_source_missing",
            "连接的来源字段没有返回内容。",
            422,
        )
        if key not in value:
            if optional:
                return MISSING
            raise PlatformError("composition_source_missing", "连接的来源字段没有返回内容。", 422)
        value = value[key]
    return deepcopy(value)


def schema_at_path(schema, path, *, required=False):
    """Resolve a declared field and, for required targets, prove every parent is present."""
    for key in path:
        require(
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and key in schema.get("properties", {})
            and key not in FORBIDDEN_KEYS,
            "composition_field",
            "数据连接需要选择接口明确声明的字段。",
            422,
        )
        if required:
            require(
                key in schema.get("required", []),
                "composition_field_optional",
                "必填目标需要连接保证提供的来源字段。",
                422,
            )
        schema = schema["properties"][key]
    return schema


def compatible(source, target):
    """Prove a conservative subset relation; runtime validation remains mandatory.

    Complex alternatives/references require an explicit adapter capability. A
    failed proof never silently converts or truncates business data.
    """
    if not isinstance(source, dict) or not isinstance(target, dict):
        return False
    annotations = {"title", "description", "examples", "default", "$schema", "$id"}
    clean = lambda value: {
        k: v
        for k, v in value.items()
        if k not in annotations and not (k == "format" and v == "textarea")
    }
    source, target = clean(source), clean(target)
    if source == target or not target:
        return True
    supported = {
        "type",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
    if set(source) - supported or set(target) - supported:
        return False
    if "const" in source:
        return Draft202012Validator(target).is_valid(source["const"])
    if "enum" in source:
        return all(Draft202012Validator(target).is_valid(value) for value in source["enum"])
    if "enum" in target or "const" in target:
        return False
    kind = source.get("type")
    if (
        not isinstance(kind, str)
        or not isinstance(target.get("type"), str)
        or target.get("type")
        not in {
            kind,
            "number" if kind == "integer" else kind,
        }
    ):
        return False
    for key in ("minLength", "minimum", "exclusiveMinimum", "minItems"):
        if key in target and (key not in source or source[key] < target[key]):
            return False
    for key in ("maxLength", "maximum", "exclusiveMaximum", "maxItems"):
        if key in target and (key not in source or source[key] > target[key]):
            return False
    for key in ("pattern", "format", "uniqueItems"):
        if key in target and source.get(key) != target[key]:
            return False
    if kind == "array":
        return compatible(source.get("items", {}), target.get("items", {}))
    if kind == "object":
        source_props, target_props = source.get("properties", {}), target.get("properties", {})
        if not set(target.get("required", [])) <= set(source.get("required", [])):
            return False
        if target.get("additionalProperties", True) is not True:
            if source.get("additionalProperties", True) is not False:
                return False
            if set(source_props) - set(target_props):
                return False
        return all(
            compatible(value, target_props.get(key, {})) for key, value in source_props.items()
        )
    return True


def validate_composition(document, flows):
    """Resolve each step against its admitted export before saving a definition."""
    errors = list(Draft202012Validator(SCHEMA).iter_errors(document))
    require(not errors, "composition_contract", "组合定义不符合当前格式。", 422)
    require(
        len(json.dumps(document, ensure_ascii=False).encode("utf-8")) <= 128_000,
        "composition_size",
        "组合定义超过大小限制。",
        413,
    )

    def declared_patterns(value):
        if isinstance(value, dict):
            if isinstance(value.get("pattern"), str):
                yield value["pattern"]
            for child in value.values():
                yield from declared_patterns(child)
        elif isinstance(value, list):
            for child in value:
                yield from declared_patterns(child)

    patterns = {
        pattern
        for flow in flows.values()
        for schema in (flow["input_schema"], flow["output_schema"])
        for pattern in declared_patterns(schema)
    }
    validate_input_schema(document["input_schema"], patterns=patterns)
    require(
        document["input_schema"].get("type") == "object",
        "composition_input",
        "组合输入应为具名字段。",
        422,
    )
    seen = {}
    for step in document["steps"]:
        require(step["id"] not in seen, "composition_duplicate", "组合步骤编号重复。", 422)
        flow = flows[step["id"]]
        target = flow["input_schema"]
        require(
            target.get("type") == "object"
            and set(step["bindings"]) <= target.get("properties", {}).keys()
            and set(target.get("required", [])) <= step["bindings"].keys(),
            "composition_input",
            "步骤需要连接所有必填字段，且只能连接已声明的输入。",
            422,
        )
        for field, binding in step["bindings"].items():
            expected = target["properties"][field]
            if binding["source"] == "literal":
                valid = Draft202012Validator(expected).is_valid(binding["value"])
            else:
                if binding["source"] == "step":
                    require(
                        binding["step_id"] in seen,
                        "composition_order",
                        "数据来源必须是前面已经完成的步骤。",
                        422,
                    )
                    origin = seen[binding["step_id"]]["output_schema"]
                else:
                    origin = document["input_schema"]
                valid = compatible(
                    schema_at_path(
                        origin, binding["path"], required=field in target.get("required", [])
                    ),
                    expected,
                )
            require(
                valid,
                "composition_type",
                f"步骤 {step['title']} 的 {field} 字段类型或取值范围不匹配。",
                422,
            )
        seen[step["id"]] = flow
    role_ids = [role["id"] for role in document["roles"]]
    require(len(role_ids) == len(set(role_ids)), "composition_role", "组合角色编号重复。", 422)
    return deepcopy(document)
