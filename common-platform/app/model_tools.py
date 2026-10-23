"""Bounded model-only tool projections; business schemas remain authoritative.

Dynamic objects are represented as JSON strings for provider compatibility and
decoded only into a reviewable form. File bytes are never requested from a model.
This layer neither changes registry schemas nor authorizes or executes a tool.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

MAX_TOOLS = 12
MAX_TOOL_BYTES = 24000
MAX_JSON_ARGUMENT_CHARS = 120000
MAX_CONTEXT_BYTES = 18000
_OMIT = object()


def _resolve(schema, root, seen=()):
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if not reference:
        return schema
    if not isinstance(reference, str) or not reference.startswith("#/") or reference in seen:
        return {}
    target = root
    for part in reference[2:].split("/"):
        target = (
            target.get(part.replace("~1", "/").replace("~0", "~"))
            if isinstance(target, dict)
            else None
        )
    if not isinstance(target, dict):
        return {}
    return _resolve(
        {**target, **{k: v for k, v in schema.items() if k != "$ref"}}, root, (*seen, reference)
    )


def _nullable(schema, root):
    types = schema.get("type")
    if isinstance(types, list) and len(types) == 2 and "null" in types:
        return {**schema, "type": next(t for t in types if t != "null")}
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list) and len(variants) == 2:
        variants = [_resolve(v, root) for v in variants]
        if sum(v.get("type") == "null" for v in variants) == 1:
            return {
                **{k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")},
                **next(v for v in variants if v.get("type") != "null"),
            }
    return None


def _file(schema, root):
    value = _resolve(schema, root)
    branch = _nullable(value, root)
    if branch is not None:
        value = _resolve(branch, root)
    return value.get("format") == "platform-file" or (
        value.get("type") == "array"
        and _resolve(value.get("items"), root).get("format") == "platform-file"
    )


def _json_field(schema, depth):
    return (
        depth > 3
        or any(key in schema for key in ("anyOf", "oneOf", "allOf", "patternProperties"))
        or isinstance(schema.get("type"), list)
        or schema.get("type") == "object"
        and (
            not schema.get("properties")
            or len(schema["properties"]) > 24
            or schema.get("additionalProperties") not in (None, False)
        )
        or schema.get("type")
        not in ("string", "number", "integer", "boolean", "object", "array", "null")
        and not schema.get("enum")
    )


def _project(schema, root, depth=0):
    schema = _resolve(schema, root)
    if _file(schema, root) or schema.get("deprecated"):
        return _OMIT
    nullable = _nullable(schema, root)
    if nullable is not None:
        value = _project(nullable, root, depth)
        return _OMIT if value is _OMIT else {"anyOf": [value, {"type": "null"}]}
    description = str(schema.get("description") or schema.get("title") or "")[:400]
    if _json_field(schema, depth):
        return {
            "type": "string",
            "description": (
                description + " 填写 JSON 字符串；只整理用户已提供的内容，未知内容留空并询问。"
            )[:500],
        }
    kind = schema.get("type")
    if not kind and schema.get("enum"):
        types = {type(value) for value in schema["enum"]}
        kind = (
            "string"
            if types == {str}
            else "boolean" if types == {bool} else "number" if types <= {int, float} else None
        )
    if not kind:
        return {"type": "string", "description": "JSON 字符串，需用户核对。"}
    result = {"type": kind}
    if description:
        result["description"] = description
    for key in ("enum", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            result[key] = deepcopy(schema[key])
    if kind == "object":
        properties = {}
        for key, child in schema.get("properties", {}).items():
            value = _project(child, root, depth + 1)
            if value is not _OMIT:
                properties[key] = value
        result.update(properties=properties, additionalProperties=False)
        required = [key for key in schema.get("required", []) if key in properties]
        if required:
            result["required"] = required
    elif kind == "array":
        items = _project(schema.get("items", {}), root, depth + 1)
        if items is _OMIT:
            return _OMIT
        result["items"] = items
    return result


def tool_parameters(schema):
    # Tool roots must always be objects, even for a parameterless capability.
    properties = {}
    for key, value in schema.get("properties", {}).items():
        projected = _project(value, schema)
        if projected is not _OMIT:
            properties[key] = projected
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "required": [key for key in schema.get("required", []) if key in properties],
    }


def _name(capability_id):
    name = re.sub(r"[^A-Za-z0-9_-]", "_", capability_id.replace(".", "__"))
    if len(name) <= 64 and "__" not in capability_id:
        return name
    return name[:47] + "_" + hashlib.sha256(capability_id.encode()).hexdigest()[:16]


def _flow_ids(hint):
    """Read only the server-generated active-flow hint, never the user message."""
    if not hint:
        return None
    try:
        start = hint.index("{")
        data, _ = json.JSONDecoder().raw_decode(hint[start:])
        if data.get("status") in {"completed", "exited", "catalog"}:
            return None
        if data.get("status") == "unavailable":
            return []
        steps, current = data["steps"], data["current_step"]
        if (
            not isinstance(steps, list)
            or not isinstance(current, int)
            or isinstance(current, bool)
            or not 0 <= current < len(steps)
        ):
            return []
        indices = [current, current + 1, current - 1]
        allowed = data.get("allowed_capability_ids")
        if allowed is not None and (
            not isinstance(allowed, list) or any(not isinstance(cid, str) for cid in allowed)
        ):
            return []
        return [
            steps[i]["capability_id"]
            for i in indices
            if 0 <= i < len(steps)
            and isinstance(steps[i], dict)
            and isinstance(steps[i].get("capability_id"), str)
            and (allowed is None or steps[i]["capability_id"] in allowed)
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        # A stale or malformed active-flow hint must never widen the menu.
        return []


def _terms(text):
    text = text.lower()
    result = set(re.findall(r"[a-z0-9_]+", text))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(part[i : i + 2] for i in range(len(part) - 1))
    return result


def build_model_tools(specs, content, *, flow_hint=""):
    """Select at most 12 already-authorized, available capabilities, never expand access."""
    candidates = [s for s in specs if s.get("availability", {}).get("available", True)]
    flow = _flow_ids(flow_hint)
    if flow is not None:
        by_id = {s["id"]: s for s in candidates}
        candidates = [by_id[cid] for cid in dict.fromkeys(flow) if cid in by_id]
    else:
        terms = _terms(content)

        def score(spec):
            title = spec.get("title", "")
            direct = 100 if spec["id"] in content else 25 if title and title in content else 0
            return (
                direct
                + 4 * len(terms & _terms(title))
                + len(terms & _terms(spec.get("description", "")))
            )

        candidates.sort(key=lambda s: -score(s))
        # With no matching phrase, spread the small menu across modules.
        if not candidates or not score(candidates[0]):
            groups = {}
            for spec in candidates:
                groups.setdefault(spec.get("module_id", ""), []).append(spec)
            candidates = [
                group[i]
                for i in range(max((len(g) for g in groups.values()), default=0))
                for group in groups.values()
                if i < len(group)
            ]
    tools, names, size = [], {}, 0
    for spec in candidates:
        name = _name(spec["id"])
        if name in names:
            name = "cap_" + hashlib.sha256(spec["id"].encode()).hexdigest()[:48]
        tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": (spec["title"] + "：" + spec.get("description", ""))[:600],
                "parameters": tool_parameters(spec["input_schema"]),
            },
        }
        length = len(json.dumps(tool, ensure_ascii=False).encode())
        if size + length > MAX_TOOL_BYTES:
            continue
        tools.append(tool)
        names[name] = spec
        size += length
        if len(tools) >= MAX_TOOLS:
            break
    return tools, names


def decode_prefill(spec, args):
    """Decode projected JSON strings into form values, removing unknown/file fields.

    Invalid JSON leaves the field unfilled. This is not business validation or
    execution: the original registry schema is still checked on form submission.
    """
    root = spec["input_schema"]

    def remove_files(schema, value, depth=0):
        schema = _resolve(schema, root)
        if depth > 20 or _file(schema, root) or schema.get("deprecated"):
            return _OMIT
        nullable = _nullable(schema, root)
        if nullable is not None:
            return None if value is None else remove_files(nullable, value, depth + 1)
        # A composition may hide a file declaration below the JSON-string
        # projection. Apply every branch conservatively before walking values.
        for key in ("anyOf", "oneOf", "allOf"):
            for branch in schema.get(key, []):
                value = remove_files(branch, value, depth + 1)
                if value is _OMIT:
                    return _OMIT
        if isinstance(value, dict):
            output = {}
            for key, child in value.items():
                child_schema = schema.get("properties", {}).get(
                    key, schema.get("additionalProperties", {})
                )
                cleaned = remove_files(child_schema, child, depth + 1)
                for pattern, pattern_schema in schema.get("patternProperties", {}).items():
                    if cleaned is not _OMIT and re.search(pattern, key):
                        cleaned = remove_files(pattern_schema, cleaned, depth + 1)
                if cleaned is not _OMIT:
                    output[key] = cleaned
            return output
        if isinstance(value, list):
            items = [remove_files(schema.get("items", {}), child, depth + 1) for child in value]
            return [item for item in items if item is not _OMIT]
        return deepcopy(value)

    def decode(schema, value, depth=0):
        schema = _resolve(schema, root)
        if _file(schema, root) or schema.get("deprecated"):
            return _OMIT
        nullable = _nullable(schema, root)
        if nullable is not None:
            return None if value is None else decode(nullable, value, depth)
        if _json_field(schema, depth):
            if isinstance(value, str):
                if len(value) > MAX_JSON_ARGUMENT_CHARS:
                    return _OMIT
                try:
                    parsed = json.loads(
                        value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                    )
                    return remove_files(schema, parsed)
                except (ValueError, RecursionError):
                    return _OMIT
            return remove_files(schema, value)
        if schema.get("type") == "object" and isinstance(value, dict):
            result = {}
            for key, child in schema.get("properties", {}).items():
                if key in value:
                    item = decode(child, value[key], depth + 1)
                    if item is not _OMIT:
                        result[key] = item
            return result
        if schema.get("type") == "array" and isinstance(value, list):
            values = [decode(schema.get("items", {}), item, depth + 1) for item in value]
            return [item for item in values if item is not _OMIT]
        return deepcopy(value)

    output = {}
    for key, schema in root.get("properties", {}).items():
        if key in args:
            value = decode(schema, args[key])
            if value is not _OMIT:
                output[key] = value
    return output


def bounded_model_messages(messages):
    """Keep the newest already-authorized text within a fixed provider context budget."""
    result, remaining = [], MAX_CONTEXT_BYTES
    truncated = "\n【资料已截断；缺失内容需用户补充，不能把片段视为完整参数。】".encode("utf-8")
    for item in reversed(messages[-10:]):
        raw = str(item.get("content", "")).encode("utf-8")
        limit = min(6000, remaining)
        if len(raw) > limit:
            if limit <= len(truncated):
                break
            text = raw[: limit - len(truncated)].decode(
                "utf-8", errors="ignore"
            ) + truncated.decode("utf-8")
        else:
            text = raw.decode("utf-8")
        if not text:
            continue
        result.append({"role": item["role"], "content": text})
        remaining -= len(text.encode("utf-8"))
        if remaining <= 0:
            break
    return list(reversed(result))
