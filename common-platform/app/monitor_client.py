"""Main API reads one bounded summary; it never ingests module load reports."""
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import httpx

from monitoring_service.protocol import SNAPSHOT_BYTES
from monitoring_service.settings import unseal
from monitoring_service.store import digest


class AggregateMonitorClient:
    INTERVAL_SECONDS = 5
    STALE_AFTER_SECONDS = 20

    def __init__(self, directory, manager, *, transport=None, clock=None):
        self.directory, self.manager = Path(directory), manager
        self.path = self.directory / "monitor-connection.json"
        self.transport, self.clock = transport, clock or time.time
        self._lock = threading.RLock()
        self._snapshot = None
        self._index = {}
        self._received = None
        self._attempted = None
        self._state = "unconfigured"
        self._task = None
        self._request_count = 0
        self._failures = 0

    def _connection(self):
        config = json.loads(self.path.read_text(encoding="utf-8"))
        from .model_gateway import ModelGateway
        url = ModelGateway._base_url(config["url"])
        if url != config["url"] or config["version"] != "1.0":
            raise ValueError()
        return config, unseal(self.directory, config["secret"])

    async def _request(self, config, token, method, path, payload=None, *, timeout=2):
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         timeout=timeout, follow_redirects=False) as client:
                self._request_count += 1
                async with client.stream(method, config["url"] + "/api/v1/monitor/" + path,
                                         headers={"Authorization": "Bearer " + token}, json=payload) as response:
                    if response.status_code != 200:
                        raise ValueError()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if len(raw) + len(chunk) > SNAPSHOT_BYTES:
                            raise ValueError()
                        raw.extend(chunk)
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))

    def _validate(self, snapshot, config):
        # Validation is independent of the collector: unexpected fields cannot
        # carry credentials into the platform/browser aggregate.
        from .monitor_contract import validate_snapshot
        validate_snapshot(snapshot)
        if snapshot["collector_id"] != config["collector_id"] or snapshot["owner_id"] != config["owner_id"]:
            raise ValueError()
        now = self.clock()
        if not -5 <= now - snapshot["generated_at"] <= self.STALE_AFTER_SECONDS:
            raise ValueError()

    async def _refresh(self):
        self._attempted = self.clock()
        if not self.path.is_file():
            self._state = "unconfigured"
            return
        try:
            config, token = self._connection()
            snapshot = await self._request(config, token, "GET", "snapshot")
            self._validate(snapshot, config)
            targets = self.manager.monitor_targets()
            if snapshot["targets_digest"] != digest(targets):
                await self._request(config, token, "PUT", "targets", {
                    "monitor_version": "1.0", "owner_id": config["owner_id"],
                    "expected_revision": snapshot["revision"], "targets": targets})
                snapshot = await self._request(config, token, "GET", "snapshot")
                self._validate(snapshot, config)
            with self._lock:
                self._snapshot, self._received = snapshot, self.clock()
                self._index = {item["id"]: item for item in snapshot["items"]}
                self._state, self._failures = "connected", 0
        except Exception:
            # No response body, URL, token or arbitrary remote message is public.
            self._state = "unavailable"
            self._failures += 1

    async def refresh(self, *, force=False):
        if self._task is not None and not self._task.done():
            return await asyncio.shield(self._task)
        if not force and self._attempted is not None and self.clock()-self._attempted < self.INTERVAL_SECONDS:
            return
        self._task = asyncio.create_task(self._refresh())
        return await asyncio.shield(self._task)

    def summary(self):
        with self._lock:
            fresh = self._received is not None and 0 <= self.clock()-self._received <= self.STALE_AFTER_SECONDS
            state = self._state if self._state != "connected" or fresh else "stale"
            active = state == "connected"
            return {"mode": "independent", "state": state, "interval_seconds": self.INTERVAL_SECONDS,
                    "last_received_at": self._received,
                    "summary": copy.deepcopy(self._snapshot["summary"]) if active else None,
                    "items": copy.deepcopy(self._snapshot["items"]) if active else [],
                    "message": {"connected": "已从独立监控服务取得汇总。", "unconfigured": "尚未配置独立监控服务。",
                                "unavailable": "独立监控服务暂不可用，模块负载与健康保持未知。",
                                "stale": "独立监控汇总已过期，请检查监控服务连接。"}[state]}

    def status(self, service_id, revision):
        with self._lock:
            fresh = self._received is not None and 0 <= self.clock()-self._received <= self.STALE_AFTER_SECONDS
            item = self._index.get(service_id)
            if self._state == "connected" and fresh and item and item["revision"] == revision:
                return copy.deepcopy(item["monitor"])
        return {"state": "unknown", "last_checked_at": None, "last_success_at": None,
                "latency_ms": None, "consecutive_failures": 0, "message": "独立监控尚无当前连接的有效健康汇总。",
                "interval_seconds": 15, "stale_after_seconds": 45}

    async def probe(self, service_id):
        from .remote_services import RemoteServiceError
        expected_revision = self.manager._monitor_revision(service_id)
        await self.refresh(force=True)
        if self.summary()["state"] != "connected":
            raise RemoteServiceError("remote_monitor_unavailable", "独立监控服务尚未配置或暂不可用。")
        try:
            config, token = self._connection()
            result = await self._request(config, token, "POST", "targets/" + service_id + "/probe", timeout=7)
            if (not isinstance(result, dict) or set(result) != {"ok", "message", "latency_ms"}
                    or type(result["ok"]) is not bool or type(result["latency_ms"]) is not int):
                raise ValueError()
            await self.refresh(force=True)
            if self.manager._monitor_revision(service_id) != expected_revision:
                raise RemoteServiceError("remote_revision_conflict", "服务连接已变化，请刷新后重新检测。")
            # The collector's arbitrary strings never become platform messages.
            return {"ok": result["ok"], "message": "远程服务连接正常。" if result["ok"] else "远程健康检查未通过。",
                    "latency_ms": result["latency_ms"]}
        except RemoteServiceError:
            raise
        except Exception:
            raise RemoteServiceError("remote_monitor_unavailable", "独立监控服务检测未完成，请刷新后重试。") from None

    def rebind_revision(self, service_id, previous_revision, revision):
        # The collector alone owns health transitions. Next control sync carries
        # endpoint-preserving revisions without falsely renewing timestamps.
        self._attempted = None

    async def run(self, stop_event):
        try:
            while not stop_event.is_set():
                await self.refresh()
                seconds = min(30, self.INTERVAL_SECONDS * 2**min(self._failures, 3))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=seconds)
                except TimeoutError:
                    pass
        finally:
            if self._task and not self._task.done():
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
