"""Strict, credential-free summary accepted by the main API."""
from datetime import datetime
from typing import Literal

from pydantic import Field, FiniteFloat, field_validator

from monitoring_service.protocol import Strict


class Health(Strict):
    state: Literal["healthy", "degraded", "offline", "stale", "unknown"]
    last_checked_at: str | None
    last_success_at: str | None
    latency_ms: int | None = Field(ge=0, le=60000)
    consecutive_failures: int = Field(ge=0)
    message: Literal["尚未完成健康检查。", "健康检查结果已过期，请等待后台检测或手动检查。", "远程服务连接正常。", "远程健康检查未通过。"]
    interval_seconds: Literal[15]
    stale_after_seconds: Literal[45]

    @field_validator("last_checked_at", "last_success_at")
    @classmethod
    def timestamp(cls, value):
        if value is not None:
            if len(value) > 40 or datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("Invalid timestamp")
        return value


class LoadMetrics(Strict):
    cpu_percent: FiniteFloat | None = Field(ge=0, le=100)
    rss_bytes: int | None = Field(ge=0, le=2**64)
    inflight_requests: int | None = Field(ge=0, le=16*10**9)
    queue_depth: int | None = Field(ge=0, le=16*10**9)
    requests_total: int | None = Field(ge=0, le=16*2**53)
    errors_total: int | None = Field(ge=0, le=16*2**53)


class Load(Strict):
    state: Literal["fresh", "stale", "unknown"]
    last_received_at: FiniteFloat | None = Field(ge=0)
    fresh_instances: int = Field(ge=0, le=16)
    reported_instances: int = Field(ge=0, le=16)
    metrics: LoadMetrics


class Item(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")
    revision: int = Field(ge=1)
    monitor: Health
    load: Load


class Summary(Strict):
    total: int = Field(ge=0, le=256)
    healthy: int = Field(ge=0, le=256)
    degraded: int = Field(ge=0, le=256)
    offline: int = Field(ge=0, le=256)
    stale: int = Field(ge=0, le=256)
    unknown: int = Field(ge=0, le=256)


class Counters(Strict):
    accepted_reports: int = Field(ge=0)
    health_requests: int = Field(ge=0)
    snapshot_reads: int = Field(ge=0)


class Snapshot(Strict):
    monitor_version: Literal["1.0"]
    owner_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    collector_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    generated_at: FiniteFloat = Field(ge=0)
    revision: int = Field(ge=0)
    targets_digest: str = Field(pattern=r"^([a-f0-9]{64})?$")
    summary: Summary
    items: list[Item] = Field(max_length=256)
    counters: Counters


def validate_snapshot(value):
    snapshot = Snapshot.model_validate(value)
    if len({item.id for item in snapshot.items}) != len(snapshot.items):
        raise ValueError("Duplicate target")
    counts = {state: 0 for state in ("healthy", "degraded", "offline", "stale", "unknown")}
    for item in snapshot.items:
        counts[item.monitor.state] += 1
        if item.load.fresh_instances > item.load.reported_instances:
            raise ValueError("Invalid instances")
    if snapshot.summary.model_dump() != {"total": len(snapshot.items), **counts}:
        raise ValueError("Invalid counts")
    return value
