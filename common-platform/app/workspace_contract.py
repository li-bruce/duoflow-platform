"""Portable, non-executable UI and invocation contracts for remote protocol 1.2."""

from __future__ import annotations

import base64
import binascii
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import require

ROLE_IDS = ["admin", "member", "researcher", "contributor", "curator", "reviewer", "system_admin"]
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_INVOKE_BYTES = 12 * 1024 * 1024
MAX_WORKSPACE_RESPONSE_BYTES = 8 * 1024 * 1024
IDENTIFIER = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{2,120}$"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def string(maximum=200, *, empty=False):
    return {"type": "string", "minLength": 0 if empty else 1, "maxLength": maximum}


def array(items, maximum=200):
    return {"type": "array", "items": items, "maxItems": maximum}


FILE_SCHEMA = {
    **obj(
        {
            "filename": string(180),
            "media_type": string(120),
            "content_base64": string(4 * ((MAX_FILE_BYTES + 2) // 3)),
        }
    ),
    "format": "platform-file",
    "description": "单个文件最多 4 MiB；正文为 base64，不接受文件路径或 URL。",
}

ACTOR_SCHEMA = obj(
    {
        "id": string(100),
        "role": {"enum": ROLE_IDS},
        "roles": {**array({"enum": ROLE_IDS}, 7), "minItems": 1, "uniqueItems": True},
        "organization_id": {"type": ["string", "null"], "maxLength": 100},
        "team_ids": {**array(string(100), 100), "uniqueItems": True},
        "name": string(100),
        "authorization_revision": {"type": "integer", "minimum": 1},
    }
)
REQUEST_SCHEMA = obj(
    {
        "protocol_version": {"const": "1.1"},
        "request_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "capability_id": IDENTIFIER,
        "capability_version": string(40),
        "input": {"type": "object"},
        "context": obj({"workspace_id": string(100), "actor": ACTOR_SCHEMA}),
    }
)
RESPONSE_SCHEMA = obj(
    {
        "protocol_version": {"const": "1.1"},
        "request_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "capability_id": IDENTIFIER,
        "capability_version": string(40),
        "summary": string(12000, empty=True),
        "data": {"type": "object"},
    }
)


def block(kind, properties, required):
    return obj({"type": {"const": kind}, "title": string(200), **properties}, ["type", *required])


ACTION_SCHEMA = obj(
    {"label": string(100), "capability_id": IDENTIFIER, "input": {"type": "object"}},
    ["label", "capability_id", "input"],
)
BLOCK_SCHEMA = {
    "oneOf": [
        block("text", {"text": string(24000, empty=True)}, ["text"]),
        block(
            "table",
            {
                "columns": array(obj({"key": string(100), "label": string(100)}), 50),
                "rows": array({"type": "object"}, 500),
                "caption": string(1000),
            },
            ["columns", "rows"],
        ),
        block(
            "metrics",
            {
                "items": array(
                    obj(
                        {
                            "label": string(100),
                            "value": {"type": ["string", "number"]},
                            "unit": string(40),
                        },
                        ["label", "value"],
                    ),
                    30,
                )
            },
            ["items"],
        ),
        block(
            "chart",
            {
                "series": array(obj({"label": string(200), "value": {"type": "number"}}), 200),
                "x_label": string(100),
                "y_label": string(100),
                "caption": string(1000),
            },
            ["series"],
        ),
        block(
            "map",
            {
                "points": array(
                    obj(
                        {
                            "label": string(200),
                            "lat": {"type": "number", "minimum": -90, "maximum": 90},
                            "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            "count": {"type": "number", "minimum": 0},
                        },
                        ["label", "lat", "lon"],
                    ),
                    500,
                ),
                "precision": string(100),
                "caption": string(1000),
            },
            ["points"],
        ),
        block(
            "evidence",
            {
                "items": array(
                    obj(
                        {
                            "quote": string(6000),
                            "feature": string(200),
                            "value": {"type": ["string", "number", "boolean", "null"]},
                            "status": string(80),
                            "source": string(500),
                        },
                        ["quote", "feature", "value", "status"],
                    ),
                    200,
                )
            },
            ["items"],
        ),
        block("actions", {"items": array(ACTION_SCHEMA, 20)}, ["items"]),
        block(
            "image",
            {
                "content_base64": string(4 * ((MAX_FILE_BYTES + 2) // 3)),
                "media_type": {"enum": ["image/png", "image/jpeg", "image/webp"]},
                "alt": string(1000),
            },
            ["content_base64", "media_type", "alt"],
        ),
        block("download", FILE_SCHEMA["properties"], ["filename", "media_type", "content_base64"]),
    ]
}
WORKSPACE_SCHEMA = {
    "type": "object",
    "properties": {"blocks": array(BLOCK_SCHEMA, 40)},
    "required": ["blocks"],
}
SKILL_SCHEMA = obj(
    {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,120}$"},
        "title": string(200),
        "description": string(3000, empty=True),
        "triggers": {**array(string(200), 20), "minItems": 1},
        "entry_capability": IDENTIFIER,
        "steps": {
            **array(
                obj({"id": string(100), "title": string(200), "capability_id": IDENTIFIER}), 20
            ),
            "minItems": 1,
        },
    }
)


def validate(schema, value, code="workspace_contract"):
    errors = list(Draft202012Validator(schema).iter_errors(value))
    require(not errors, code, "内容不符合当前框架契约，请检查字段与类型。", 422)


def validate_files(schema, payload):
    """Check actual bytes as well as declared input structure; never read local paths."""
    if not isinstance(schema, dict):
        return
    if schema.get("format") == "platform-file" and payload is not None:
        validate(FILE_SCHEMA, payload, "file_contract")
        name = payload["filename"]
        require(
            name not in {".", ".."} and not any(c in name for c in "/\\\r\n\x00"),
            "file_name",
            "文件名无效。",
            422,
        )
        try:
            content = base64.b64decode(payload["content_base64"], validate=True)
        except (binascii.Error, ValueError):
            require(False, "file_encoding", "文件编码无效。", 422)
        require(0 < len(content) <= MAX_FILE_BYTES, "file_size", "文件为空或超过 4 MiB。", 413)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            validate_files(schema.get("properties", {}).get(key, {}), value)
    elif isinstance(payload, list):
        for value in payload:
            validate_files(schema.get("items", {}), value)


def validate_workspace(data):
    validate(WORKSPACE_SCHEMA, data)
    for item in data["blocks"]:
        if item["type"] in {"download", "image"}:
            file = {key: item[key] for key in ("media_type", "content_base64")}
            file["filename"] = item.get("filename", "image")
            validate_files(FILE_SCHEMA, file)
    return data


def normalize_skills(skills, capabilities):
    require(
        isinstance(skills, list) and len(skills) <= 30,
        "skill_contract",
        "流程声明最多 30 项。",
        422,
    )
    known = {c["id"] for c in capabilities}
    ids = set()
    for skill in skills:
        validate(SKILL_SCHEMA, skill, "skill_contract")
        require(
            skill["id"] not in ids and skill["entry_capability"] in known,
            "skill_contract",
            "流程标识重复或入口能力不存在。",
            422,
        )
        ids.add(skill["id"])
        require(
            skill["steps"][0]["capability_id"] == skill["entry_capability"],
            "skill_contract",
            "流程第一步必须为入口能力。",
            422,
        )
        require(
            len({s["id"] for s in skill["steps"]}) == len(skill["steps"])
            and all(s["capability_id"] in known for s in skill["steps"]),
            "skill_contract",
            "流程步骤标识重复或能力不存在。",
            422,
        )
    return copy.deepcopy(skills)


def write_public_schemas(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in {
        "invoke-request-1.1": REQUEST_SCHEMA,
        "invoke-response-1.1": RESPONSE_SCHEMA,
        "workspace": WORKSPACE_SCHEMA,
        "platform-file": FILE_SCHEMA,
        "skill": SKILL_SCHEMA,
    }.items():
        (directory / (name + ".schema.json")).write_text(
            json.dumps(
                {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
