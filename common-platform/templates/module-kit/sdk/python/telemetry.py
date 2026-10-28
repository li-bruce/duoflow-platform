"""Optional bounded process reporter; direct to the independent monitor."""
import os
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx
import psutil


class TelemetryReporter:
    def __init__(self, *, url, token, instance_id=None, interval_seconds=15, metrics=None, transport=None):
        parsed = urlsplit(url)
        self.instance_id = instance_id or socket.gethostname()
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not re.fullmatch(r"/api/v1/monitor/reports/[a-z][a-z0-9_.-]{1,79}", parsed.path)
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
                or not isinstance(token, str) or not 24 <= len(token) <= 4096
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.instance_id)
                or type(interval_seconds) not in {int,float} or not math.isfinite(interval_seconds)
                or not 1 <= interval_seconds <= 300):
            raise ValueError("Invalid telemetry configuration")
        self.url, self.token, self.interval = url, token, interval_seconds
        self.metrics, self.transport = metrics or (lambda: {}), transport
        self.boot_id, self.sequence = uuid.uuid4().hex, 0
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.started = time.monotonic()
        self.stopped, self.lock = threading.Event(), threading.Lock()
        self.thread = None
        self.sent = self.failed = self.skipped = 0

    def report(self):
        if self.stopped.is_set() or not self.lock.acquire(blocking=False):
            self.skipped += 1
            return
        try:
            values = self.metrics()
            if not isinstance(values, dict) or set(values) - {"inflight_requests", "queue_depth", "requests_total", "errors_total"}:
                raise ValueError()
            if any(type(v) is not int or not 0 <= v <= 2**53 for v in values.values()):
                raise ValueError()
            self.sequence += 1
            payload = {"monitor_version": "1.0", "instance_id": self.instance_id,
                       "boot_id": self.boot_id, "sequence": self.sequence, "status": "ready",
                       "uptime_seconds": time.monotonic()-self.started,
                       "metrics": {"cpu_percent": min(100, self.process.cpu_percent()/max(1, os.cpu_count() or 1)),
                                   "rss_bytes": self.process.memory_info().rss, **values}}
            with httpx.Client(transport=self.transport, timeout=2, trust_env=False, follow_redirects=False) as client:
                with client.stream("POST", self.url, headers={"Authorization": "Bearer "+self.token}, json=payload) as response:
                    if response.is_success:
                        self.sent += 1
                    else:
                        self.failed += 1
        except Exception:
            self.failed += 1
        finally:
            self.lock.release()

    def start(self):
        if self.thread or self.stopped.is_set():
            return

        def loop():
            while not self.stopped.is_set():
                self.report()
                self.stopped.wait(self.interval)

        self.thread = threading.Thread(target=loop, daemon=True, name="duoflow-telemetry")
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=3)
