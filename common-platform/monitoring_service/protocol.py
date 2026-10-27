"""Portable monitoring control and report contracts; no business payloads."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

VERSION = "1.0"
MAX_TARGETS = 256
MAX_INSTANCES = 16
REPORT_BYTES = 8192
SNAPSHOT_BYTES = 1024 * 1024
REPORT_TTL_SECONDS = 45


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Metrics(Strict):
    cpu_percent: FiniteFloat | None = Field(default=None, ge=0, le=100)
    rss_bytes: int | None = Field(default=None, ge=0, le=2**60)
    inflight_requests: int | None = Field(default=None, ge=0, le=10**9)
    queue_depth: int | None = Field(default=None, ge=0, le=10**9)
    requests_total: int | None = Field(default=None, ge=0, le=2**53)
    errors_total: int | None = Field(default=None, ge=0, le=2**53)


class Report(Strict):
    monitor_version: Literal["1.0"]
    instance_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    boot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sequence: int = Field(ge=1, le=2**53)
    status: Literal["ready", "degraded", "draining"]
    uptime_seconds: FiniteFloat = Field(ge=0, le=10**10)
    metrics: Metrics


class Target(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")
    revision: int = Field(ge=1)
    base_url: str = Field(min_length=1, max_length=2048)
    token: str = Field(max_length=4096)


class Targets(Strict):
    monitor_version: Literal["1.0"]
    owner_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: int = Field(ge=0)
    targets: list[Target] = Field(max_length=MAX_TARGETS)
