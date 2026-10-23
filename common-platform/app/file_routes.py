"""Same-origin upload/job routes bound to server-owned flow instances.

``resolve(instance_id, actor, operation)`` must load/authorize the installed
flow and return its declared capability plus its bound business object. It may
be sync or async. Neither value is accepted from request JSON.
"""

from __future__ import annotations

import inspect
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .errors import require
from .workflow_transport import IDENTIFIER, MAX_CHUNK_BYTES


_CHUNK_PATH = re.compile(
    r"^/api/v1/flows/instances/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/uploads/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/chunks/[0-9]{1,6}$"
)


def is_upload_chunk_request(path, method):
    """Used by the main boundary to skip whole-request buffering on this path only.

    The boundary should retain origin/session/security-header checks and reject
    a declared length over MAX_CHUNK_BYTES. The route checks actual stream bytes.
    """
    return method == "PUT" and _CHUNK_PATH.fullmatch(path) is not None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadCreate(Strict):
    filename: str = Field(min_length=1, max_length=180, pattern=r"^[^/\\\x00-\x1f\x7f]+$")
    size: int = Field(ge=1, le=9007199254740991, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ExpectedRevision(Strict):
    expected_revision: int = Field(ge=1, strict=True)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


def create_file_router(transport, resolve, require_user, check_csrf):
    router = APIRouter(prefix="/api/v1/flows/instances")

    async def bound(instance_id, actor, operation, payload=None):
        result = resolve(instance_id, actor, operation)
        capability_id, object_ref = await result if inspect.isawaitable(result) else result
        require(
            isinstance(capability_id, str) and isinstance(object_ref, dict),
            "flow_binding",
            "流程尚未绑定业务对象或能力。",
            409,
        )
        # A fresh copy prevents a handler from mutating the flow's saved binding.
        from copy import deepcopy

        return capability_id, {**(payload or {}), "object_ref": deepcopy(object_ref)}

    def identifier(value):
        require(
            IDENTIFIER.fullmatch(value) is not None,
            "workflow_identifier",
            "文件或任务编号无效。",
            422,
        )
        return value

    @router.post("/{instance_id}/uploads")
    async def create(instance_id: str, body: UploadCreate, actor=Depends(check_csrf)):
        cap, payload = await bound(instance_id, actor, "uploads.create", body.model_dump())
        return await transport.request(actor, cap, "uploads.create", payload)

    @router.get("/{instance_id}/uploads/{upload_id}")
    async def status(instance_id: str, upload_id: str, actor=Depends(require_user)):
        cap, payload = await bound(
            instance_id, actor, "uploads.status", {"upload_id": identifier(upload_id)}
        )
        return await transport.request(actor, cap, "uploads.status", payload)

    @router.put("/{instance_id}/uploads/{upload_id}/chunks/{index}")
    async def chunk(
        instance_id: str, upload_id: str, index: int, request: Request, actor=Depends(check_csrf)
    ):
        require(0 <= index <= 100_000, "upload_index", "文件分块编号无效。", 422)
        identifier(upload_id)
        cap, payload = await bound(instance_id, actor, "uploads.chunk")
        require(
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            == "application/octet-stream",
            "upload_media_type",
            "文件分块必须使用 application/octet-stream。",
            415,
        )
        require(
            request.headers.get("content-encoding", "identity").lower() == "identity",
            "upload_encoding",
            "文件分块不能使用压缩传输编码。",
            415,
        )
        declared = request.headers.get("content-length")
        require(
            declared is None or declared.isdigit(), "upload_chunk_size", "文件分块长度无效。", 400
        )
        require(
            declared is None or int(declared) <= MAX_CHUNK_BYTES,
            "upload_chunk_size",
            "文件分块超过 1 MiB。",
            413,
        )
        received = bytearray()
        async for part in request.stream():
            require(
                len(received) + len(part) <= MAX_CHUNK_BYTES,
                "upload_chunk_size",
                "文件分块超过 1 MiB。",
                413,
            )
            received.extend(part)
        require(
            declared is None or int(declared) == len(received),
            "upload_chunk_size",
            "文件分块实际长度不符。",
            400,
        )
        return await transport.upload_chunk(
            actor,
            cap,
            payload["object_ref"],
            upload_id,
            index,
            bytes(received),
            sha256=request.headers.get("x-chunk-sha256"),
        )

    async def finish(instance_id, upload_id, body, actor, operation):
        cap, payload = await bound(
            instance_id,
            actor,
            operation,
            {**body.model_dump(exclude_none=True), "upload_id": identifier(upload_id)},
        )
        return await transport.request(actor, cap, operation, payload)

    @router.post("/{instance_id}/uploads/{upload_id}/complete")
    async def complete(
        instance_id: str, upload_id: str, body: ExpectedRevision, actor=Depends(check_csrf)
    ):
        return await finish(instance_id, upload_id, body, actor, "uploads.complete")

    @router.post("/{instance_id}/uploads/{upload_id}/abort")
    async def abort(
        instance_id: str, upload_id: str, body: ExpectedRevision, actor=Depends(check_csrf)
    ):
        return await finish(instance_id, upload_id, body, actor, "uploads.abort")

    @router.get("/{instance_id}/jobs/{job_id}")
    async def job_status(instance_id: str, job_id: str, actor=Depends(require_user)):
        cap, payload = await bound(
            instance_id, actor, "jobs.status", {"job_id": identifier(job_id)}
        )
        return await transport.request(actor, cap, "jobs.status", payload)

    @router.get("/{instance_id}/jobs/{job_id}/result")
    async def job_result(instance_id: str, job_id: str, actor=Depends(require_user)):
        cap, payload = await bound(
            instance_id, actor, "jobs.result", {"job_id": identifier(job_id)}
        )
        return await transport.request(actor, cap, "jobs.result", payload)

    @router.post("/{instance_id}/jobs/{job_id}/cancel")
    async def job_cancel(
        instance_id: str, job_id: str, body: ExpectedRevision, actor=Depends(check_csrf)
    ):
        cap, payload = await bound(
            instance_id,
            actor,
            "jobs.cancel",
            {**body.model_dump(exclude_none=True), "job_id": identifier(job_id)},
        )
        return await transport.request(actor, cap, "jobs.cancel", payload)

    return router
