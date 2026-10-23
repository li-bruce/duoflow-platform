"""Bounded, process-local heartbeat state independent of service publication."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import threading
import time


class ServiceMonitor:
    INTERVAL_SECONDS = 15
    TIMEOUT_SECONDS = 5
    STALE_AFTER_SECONDS = 45
    FAILURE_THRESHOLD = 3
    MAX_CONCURRENCY = 4

    def __init__(self, manager, *, clock=None):
        self.manager = manager
        self.clock = clock or time.time
        self._states = {}
        self._lock = threading.RLock()
        self._inflight = {}
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        self._running = False

    @staticmethod
    def _timestamp(value):
        return (
            datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None
        )

    def status(self, service_id, revision):
        with self._lock:
            value = self._states.get(service_id)
            if value is None or value["revision"] != revision:
                value = {
                    "revision": revision,
                    "state": "unknown",
                    "checked": None,
                    "success": None,
                    "latency_ms": None,
                    "consecutive_failures": 0,
                    "message": "尚未完成健康检查。",
                }
                self._states[service_id] = value
            state, message = value["state"], value["message"]
            if (
                value["checked"] is not None
                and self.clock() - value["checked"] > self.STALE_AFTER_SECONDS
            ):
                state, message = "stale", "健康检查结果已过期，请等待后台检测或手动检查。"
            return {
                "state": state,
                "last_checked_at": self._timestamp(value["checked"]),
                "last_success_at": self._timestamp(value["success"]),
                "latency_ms": value["latency_ms"],
                "consecutive_failures": value["consecutive_failures"],
                "message": message,
                "interval_seconds": self.INTERVAL_SECONDS,
                "stale_after_seconds": self.STALE_AFTER_SECONDS,
            }

    def record(self, service_id, revision, *, ok, message, latency_ms):
        with self._lock:
            self.status(service_id, revision)
            value = self._states[service_id]
            value["checked"] = self.clock()
            value["latency_ms"] = latency_ms
            value["message"] = message
            if ok:
                value.update(state="healthy", success=value["checked"], consecutive_failures=0)
            else:
                value["consecutive_failures"] += 1
                value["state"] = (
                    "offline"
                    if value["consecutive_failures"] >= self.FAILURE_THRESHOLD
                    else "degraded"
                )
            return self.status(service_id, revision)

    def rebind_revision(self, service_id, previous_revision, revision):
        """Carry health across publication only, without renewing its timestamps.

        The manager calls this after its atomic publication write succeeds. A
        connection/credential or manifest edit deliberately does not use it.
        """
        with self._lock:
            value = self._states.get(service_id)
            if value is not None and value["revision"] == previous_revision:
                self._states[service_id] = {**value, "revision": revision}

    async def _probe(self, service_id, revision):
        async with self._semaphore:
            return await self.manager._probe_health(service_id, expected_revision=revision)

    async def probe(self, service_id):
        # There is no await between the lookup and insertion: on the API loop,
        # manual and background probes share one request for a given revision.
        # Updating an endpoint or token must not attach a new check to old work.
        key = (service_id, self.manager._monitor_revision(service_id))
        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._probe(*key))
            self._inflight[key] = task

            def completed(done):
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if its HTTP client disconnected.

            task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def poll_once(self):
        # All configured connections are monitored, including unpublished ones.
        # A failure of one probe never prevents other services from being checked.
        try:
            targets = self.manager._monitor_targets()
        except Exception:
            return
        await asyncio.gather(
            *(self.probe(service_id) for service_id in targets), return_exceptions=True
        )

    async def _wait(self, stop_event, seconds):
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def run(self, stop_event):
        if self._running:
            return
        self._running = True
        try:
            while not stop_event.is_set():
                cycle_started = asyncio.get_running_loop().time()
                batch = asyncio.create_task(self.poll_once())
                stopping = asyncio.create_task(stop_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        (batch, stopping), return_when=asyncio.FIRST_COMPLETED
                    )
                    if stopping in done:
                        batch.cancel()
                        await asyncio.gather(batch, return_exceptions=True)
                        break
                    await batch
                finally:
                    stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
                    if not batch.done():
                        batch.cancel()
                        await asyncio.gather(batch, return_exceptions=True)
                elapsed = asyncio.get_running_loop().time() - cycle_started
                await self._wait(stop_event, max(0, self.INTERVAL_SECONDS - elapsed))
        finally:
            pending = list(self._inflight.values())
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._running = False
