"""Read admitted source objects with the current consumer's real actor.

Remote consumers retain their own business rules and independently chosen data
adapters. This seam resolves only registered object readers and never delegates
the consumer ticket or returns source credentials.
"""

from copy import deepcopy
import json
from pathlib import Path

from fastapi import APIRouter, Request
from jsonschema import Draft202012Validator

from .errors import PlatformError, require
from .identity import refresh_actor
from .job_contract import validate_reference


MAX_REQUEST_BYTES = 16 * 1024
MAX_SOURCE_DEPTH = 4
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts/access/v1/source-read-request.schema.json")
    .read_text(encoding="utf-8")
)


class SourceReads:
    def __init__(self, runtime):
        self.runtime = runtime

    def consumer(self, ticket, body):
        decision = self.runtime.access_bridge.decide(
            ticket, body["request_id"], body["permission"], body["facts"],
            operation=body["operation"],
        )
        require(decision["allowed"], decision["reason"], "当前目标业务授权无效。", 403)
        claims = self.runtime.access_bridge._verify(ticket)
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, claims["actor"], require_unchanged=True)
        return claims, actor

    async def read(self, ticket, body):
        require(
            Draft202012Validator(SCHEMA).is_valid(body),
            "source_read_contract", "来源读取输入不符合契约。", 422,
        )
        claims, actor = self.consumer(ticket, body)
        source = body["source"]
        validate_reference(source["object_ref"])
        chain = claims.get("source_chain", []) + [claims["binding"]["module_id"]]
        require(
            len(chain) <= MAX_SOURCE_DEPTH and len(set(chain)) == len(chain)
            and source["module_id"] not in chain,
            "source_read_cycle", "来源读取形成循环或超过层数限制。", 409,
        )
        catalog = self.runtime.module_catalog.module(actor, source["module_id"])
        obj = next(
            (item for item in catalog["registration"]["data_objects"]
             if item["id"] == source["object_ref"]["object_type"]), None,
        )
        reader = obj and obj["operations"].get("read")
        require(reader, "source_reader_missing", "来源对象没有登记可用的读取接口。", 422)
        spec = self.runtime.registry.get(reader, actor)
        require(
            spec["module_id"] == source["module_id"] and spec["kind"] == "query",
            "source_reader_invalid", "来源接口不是该模块的已登记查询。", 422,
        )
        payload = {**deepcopy(body.get("read_input", {})), "object_ref": deepcopy(source["object_ref"])}
        result = await self.runtime.workflows.transport.request(
            actor, reader, "objects.read", payload, source_chain=chain,
        )
        # The existing transport checks the source actor, binding, response
        # budget and formal projection. Recheck the consumer after network IO.
        self.consumer(ticket, body)
        projection = result["projection"]
        require(
            projection["object_ref"] == source["object_ref"]
            and projection["revision"] == source["revision"],
            "source_revision_changed", "来源对象或固定版本不一致。", 409,
        )
        response = {"source_read_version": "1.0", "source": deepcopy(source), "projection": deepcopy(projection)}
        require(
            ticket not in json.dumps(response, ensure_ascii=False),
            "source_read_credential", "来源响应包含调用凭据。", 502,
        )
        return response


def create_source_read_router(service):
    router = APIRouter()

    @router.post("/api/v1/access/source-reads")
    async def read(request: Request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            require(len(content) <= MAX_REQUEST_BYTES, "source_read_size", "来源读取请求过大。", 413)
        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            raise PlatformError("source_read_contract", "来源读取需要有效 JSON。", 422) from None
        return await service.read(request.headers.get("X-Platform-Access", ""), body)

    return router
