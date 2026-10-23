"""Non-executable file/job and business-reference contracts for workflow adapters."""

from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import require


ROOT = Path(__file__).resolve().parents[1] / "contracts"


@lru_cache(maxsize=None)
def schema(name):
    paths = {
        "adapter": "workflows/v1/adapter.schema.json",
        "file": "files/v1/file-ref.schema.json",
        "upload": "files/v1/upload.schema.json",
        "job": "jobs/v1/job.schema.json",
    }
    value = json.loads((ROOT / paths[name]).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def validate_reference(value):
    definition = schema("adapter")["$defs"]["object_ref"]
    require(
        Draft202012Validator(definition).is_valid(value),
        "workflow_object",
        "业务对象引用不符合契约。",
        422,
    )


def validate_page_input(operation, payload):
    definition = schema("adapter")
    name = "object_list_input" if operation == "objects.list" else "object_detail_input"
    validator = Draft202012Validator({"$ref": "#/$defs/" + name, "$defs": definition["$defs"]})
    require(validator.is_valid(payload), "workflow_page", "分页请求不符合契约。", 422)


def validate_observation(name, value):
    """Adapters may use this on structured observations; domain data stays opaque."""
    require(name in {"file", "upload", "job"}, "workflow_contract", "未知观察类型。", 500)
    require(
        Draft202012Validator(schema(name)).is_valid(value),
        "workflow_response",
        "文件或任务响应不符合契约。",
        502,
    )
    if name == "upload":
        require(
            value["offset"] <= value["size"], "workflow_response", "上传进度超过文件大小。", 502
        )
    if name == "job":
        require(
            value["progress"]["completed"] <= value["progress"]["total"],
            "workflow_response",
            "任务完成数量超过总数。",
            502,
        )


def validate_operation_result(operation, data, object_ref=None, *, payload=None, legacy_list=False):
    """Validate portable observations; never interpret a business state/event."""
    require(isinstance(data, dict), "workflow_response", "流程响应必须为对象。", 502)
    required = {
        "objects.list": {"objects", "next_cursor"},
        "objects.read": {"projection"},
        "objects.detail": {"object_ref", "revision", "items", "next_cursor", "total"},
        "uploads.create": {"upload", "projection"},
        "uploads.status": {"upload", "projection"},
        "uploads.chunk": {"upload"},
        "uploads.complete": {"file", "projection"},
        "uploads.abort": {"projection", "aborted"},
        "jobs.status": {"job"},
        "jobs.result": {"job", "result", "projection"},
        "jobs.cancel": {"job", "projection"},
    }
    if operation == "objects.list" and legacy_list and set(data) == {"objects"}:
        data["next_cursor"] = None
    require(
        operation in required and set(data) == required[operation],
        "workflow_response",
        "流程响应字段不符合操作契约。",
        502,
    )
    definition = schema("adapter")
    if operation in {"objects.list", "objects.detail"}:
        name = "object_list_result" if operation == "objects.list" else "object_detail_result"
        validator = Draft202012Validator({"$ref": "#/$defs/" + name, "$defs": definition["$defs"]})
        require(validator.is_valid(data), "workflow_response", "分页响应不符合契约。", 502)
        values = data["objects"] if operation == "objects.list" else data["items"]
        require(
            len(values) <= (payload or {}).get("limit", 20),
            "workflow_response",
            "分页响应超过请求条数。",
            502,
        )
        if operation == "objects.detail":
            require(
                data["object_ref"] == object_ref and len(values) <= data["total"],
                "workflow_response",
                "详情所属对象或总数不符合请求。",
                502,
            )
        elif (payload or {}).get("object_type"):
            require(
                all(item["object_ref"]["object_type"] == payload["object_type"] for item in values),
                "workflow_response",
                "分页对象类型与请求不一致。",
                502,
            )
    projection_validator = Draft202012Validator(
        {"$ref": "#/$defs/projection", "$defs": definition["$defs"]}
    )
    projections = (
        data.get("objects", [])
        if operation == "objects.list"
        else [data["projection"]] if "projection" in data else []
    )
    require(
        isinstance(projections, list) and len(projections) <= 50,
        "workflow_response",
        "业务对象列表不符合契约。",
        502,
    )
    for projection in projections:
        require(
            projection_validator.is_valid(projection),
            "workflow_response",
            "业务对象投影不符合契约。",
            502,
        )
        if object_ref is not None:
            require(
                projection["object_ref"] == object_ref,
                "workflow_response",
                "流程响应对象与请求不一致。",
                502,
            )
    for name in ("upload", "file", "job"):
        if name in data:
            validate_observation(name, data[name])
            if name != "file" and object_ref is not None:
                require(
                    data[name]["object_ref"] == object_ref,
                    "workflow_response",
                    "文件或任务所属对象与请求不一致。",
                    502,
                )
    if "aborted" in data:
        require(data["aborted"] is True, "workflow_response", "上传终止响应不符合契约。", 502)
    if "result" in data:
        require(
            data["result"] is None or isinstance(data["result"], dict),
            "workflow_response",
            "任务结果不符合契约。",
            502,
        )
