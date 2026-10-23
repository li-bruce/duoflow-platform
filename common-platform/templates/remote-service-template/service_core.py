"""Small HTTP/JSON adapter: no imports from the host platform."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, ValidationError
from starlette.exceptions import HTTPException

PROTOCOL = "1.0"
REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["protocol_version", "request_id", "capability_id", "capability_version", "input", "context"],
    "properties": {
        "protocol_version": {"const": PROTOCOL},
        "request_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "capability_id": {"type": "string", "minLength": 1},
        "capability_version": {"type": "string", "minLength": 1},
        "input": {"type": "object"},
        "context": {
            "type": "object", "additionalProperties": False,
            "required": ["workspace_id", "actor"],
            "properties": {
                "workspace_id": {"type": "string", "minLength": 1},
                "actor": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "role"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "role": {"enum": ["admin", "member"]},
                    },
                },
            },
        },
    },
}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def create_app(service_dir: Path, handler: Callable, data_dir: Path | None = None) -> FastAPI:
    service_dir = service_dir.resolve()
    data_dir = Path(data_dir or os.environ.get("REMOTE_SERVICE_DATA_DIR", service_dir / "data")).resolve()
    configuration = json.loads((data_dir / "service-config.json").read_text(encoding="utf-8-sig"))
    token = configuration.get("service_token")
    if not isinstance(token, str) or len(token) < 32:
        raise RuntimeError("Configure a service_token of at least 32 characters before starting.")
    manifest = json.loads((service_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol_version") not in {"1.0", "1.1"}:
        raise RuntimeError("Unsupported manifest protocol version.")
    capabilities = {item["id"]: item for item in manifest["capabilities"]}
    for capability in capabilities.values():
        Draft202012Validator.check_schema(capability["input_schema"])
        Draft202012Validator.check_schema(capability["output_schema"])
    database = data_dir / "requests.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

    app = FastAPI(title=manifest["module"]["name"], docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        return JSONResponse({"error": {"code": "http_error", "message": "请求的路径或方法不可用。"}}, status_code=error.status_code)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse({"error": {"code": "unauthorized", "message": "需要有效的服务令牌。"}}, status_code=401)
        return await call_next(request)

    @app.get("/platform/v1/manifest")
    async def get_manifest():
        return manifest

    @app.get("/platform/v1/health")
    async def health():
        return {"status": "ok", "service": manifest["module"]["id"], "protocol_version": PROTOCOL}

    def execute(payload: dict) -> dict:
        try:
            Draft202012Validator(REQUEST_SCHEMA).validate(payload)
        except ValidationError:
            raise ServiceError(422, "invalid_request", "请求不符合远程服务协议。") from None
        fingerprint = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
        try:
            connection = sqlite3.connect(database, timeout=5, isolation_level=None)
        except sqlite3.OperationalError:
            raise ServiceError(503, "service_busy", "无法打开请求缓存，请使用原请求编号重试。") from None
        try:
            # This demo's deterministic handler completes inside this transaction.
            # Concurrent identical request IDs cannot execute the handler twice.
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT fingerprint, response_json FROM requests WHERE request_id = ?", (payload["request_id"],)).fetchone()
            if prior is not None:
                if prior[0] != fingerprint:
                    raise ServiceError(409, "idempotency_conflict", "同一请求编号已用于不同内容。")
                response = json.loads(prior[1])
            else:
                capability = capabilities.get(payload["capability_id"])
                if capability is None:
                    raise ServiceError(404, "capability_not_found", "服务未提供请求的能力。")
                if payload["capability_version"] != capability["version"]:
                    raise ServiceError(409, "capability_version_mismatch", "请求能力版本与当前服务不一致。")
                if payload["context"]["actor"]["role"] not in capability["roles"]:
                    raise ServiceError(403, "forbidden", "当前身份不能调用此能力。")
                try:
                    Draft202012Validator(capability["input_schema"]).validate(payload["input"])
                except ValidationError:
                    raise ServiceError(422, "invalid_input", "输入不符合能力参数定义。") from None
                summary, data = handler(payload["capability_id"], payload["input"], payload["context"])
                Draft202012Validator(capability["output_schema"]).validate(data)
                if not isinstance(summary, str) or not 1 <= len(summary) <= 500:
                    raise ValueError("The handler summary must contain 1-500 characters.")
                response = {
                    "protocol_version": PROTOCOL,
                    "request_id": payload["request_id"],
                    "capability_id": payload["capability_id"],
                    "capability_version": payload["capability_version"],
                    "summary": summary,
                    "data": data,
                }
                connection.execute("INSERT INTO requests (request_id, fingerprint, response_json) VALUES (?, ?, ?)", (payload["request_id"], fingerprint, canonical(response)))
            connection.commit()
            return response
        except ServiceError:
            connection.rollback()
            raise
        except sqlite3.OperationalError:
            connection.rollback()
            raise ServiceError(503, "service_busy", "服务暂时繁忙，请使用原请求编号重试。") from None
        except Exception:
            connection.rollback()
            raise ServiceError(500, "handler_failed", "服务处理失败；本次结果未写入幂等缓存。") from None
        finally:
            connection.close()

    @app.post("/platform/v1/invoke")
    async def invoke(request: Request):
        body = await request.body()
        if len(body) > 256 * 1024:
            raise ServiceError(413, "request_too_large", "请求超过此示例的 256 KiB 大小上限。")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise ServiceError(400, "invalid_json", "请求正文需要有效的 JSON。") from None
        return await asyncio.to_thread(execute, payload)

    return app
