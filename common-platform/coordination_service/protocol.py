"""Strict metadata-only business event and coordination control contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_BYTES = 8192
CONFIG_BYTES = 512 * 1024
LEASE_SECONDS = 30
KINDS = {"state.changed", "publication.requested", "resource.published", "resource.withdrawn"}


class CoordinationError(Exception):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Scope(Strict):
    organization_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class ObjectRef(Strict):
    object_type: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    object_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")


class Event(Strict):
    version: Literal["1.0"]
    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    module_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: Literal["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]
    machine_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    object_ref: ObjectRef
    state: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    revision: int = Field(ge=1, le=2**53 - 1)
    scope: Scope
    approval_ref: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class Machine(Strict):
    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    object_types: list[str] = Field(min_length=1, max_length=50)
    states: list[str] = Field(min_length=1, max_length=100)
    public_states: list[str] = Field(default_factory=list, max_length=100)
    approval_required: bool = False

    @model_validator(mode="after")
    def lists(self):
        import re
        for values, maximum in ((self.object_types, 160), (self.states, 128), (self.public_states, 128)):
            if len(values) != len(set(values)) or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1," + str(maximum) + "}", v) for v in values):
                raise ValueError("invalid declaration list")
        if not set(self.public_states) <= set(self.states):
            raise ValueError("unknown public state")
        return self


class Accept(Strict):
    source_module: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: Literal["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]
    mode: Literal["notify", "sync"]


class Module(Strict):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    token: str = Field(min_length=16, max_length=512)
    enabled: bool
    machines: list[Machine] = Field(default_factory=list, max_length=50)
    emits: list[Literal["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]] = Field(default_factory=list, max_length=4)
    accepts: list[Accept] = Field(default_factory=list, max_length=200)


class Subscription(Strict):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    source_module: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    target_module: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: Literal["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]
    scope: Scope
    mode: Literal["notify", "sync"]


class Config(Strict):
    expected_revision: int = Field(ge=0)
    modules: list[Module] = Field(max_length=100)
    subscriptions: list[Subscription] = Field(max_length=500)

    @model_validator(mode="after")
    def declarations(self):
        modules = {m.id: m for m in self.modules}
        if len(modules) != len(self.modules) or len({m.token for m in self.modules}) != len(self.modules):
            raise ValueError("duplicate module or token")
        if len({s.id for s in self.subscriptions}) != len(self.subscriptions):
            raise ValueError("duplicate subscription")
        for m in self.modules:
            if len({v.id for v in m.machines}) != len(m.machines) or len(set(m.emits)) != len(m.emits):
                raise ValueError("duplicate machine or emission")
            accepts = [(v.source_module, v.kind, v.mode) for v in m.accepts]
            if len(set(accepts)) != len(accepts):
                raise ValueError("duplicate acceptance")
            for machine in m.machines:
                if not machine.id.startswith(m.id + ".") or any(not v.startswith(m.id + ".") for v in machine.object_types):
                    raise ValueError("machine ownership")
        for s in self.subscriptions:
            source, target = modules.get(s.source_module), modules.get(s.target_module)
            if source is None or target is None or s.kind not in source.emits or not any(
                a.source_module == s.source_module and a.kind == s.kind and a.mode == s.mode for a in target.accepts
            ):
                raise ValueError("undeclared subscription")
            if s.kind == "publication.requested":
                raise ValueError("publication requests are private approvals")
        return self


class Decision(Strict):
    expected_revision: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    actor_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class Ack(Strict):
    consumer_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    lease_token: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class SummaryObject(Strict):
    module_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    machine_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    object_ref: ObjectRef
    state: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    revision: int = Field(ge=1, le=2**53 - 1)
    scope: Scope
    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    updated_at: float = Field(ge=0, allow_inf_nan=False)


class SummaryApproval(Strict):
    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    module_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    machine_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    object_ref: ObjectRef
    state: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    object_revision: int = Field(ge=1, le=2**53 - 1)
    scope: Scope
    status: Literal["pending", "approved", "rejected"]
    revision: int = Field(ge=1, le=2)
    actor_id: str | None = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    created_at: float = Field(ge=0, allow_inf_nan=False)
    decided_at: float | None = Field(ge=0, allow_inf_nan=False)
    used_by: str | None = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def lifecycle(self):
        if self.status == "pending":
            if self.revision != 1 or self.actor_id is not None or self.decided_at is not None or self.used_by is not None:
                raise ValueError("inconsistent pending approval")
        elif self.revision != 2 or self.actor_id is None or self.decided_at is None or self.decided_at < self.created_at:
            raise ValueError("inconsistent decided approval")
        if self.status != "approved" and self.used_by is not None:
            raise ValueError("only approved requests can be used")
        return self


class SummaryCounts(Strict):
    events: int = Field(ge=0, le=2**53 - 1)
    objects: int = Field(ge=0, le=2**53 - 1)
    approvals_pending: int = Field(ge=0, le=2**53 - 1)
    deliveries_pending: int = Field(ge=0, le=2**53 - 1)
    deliveries_leased: int = Field(ge=0, le=2**53 - 1)
    deliveries_acked: int = Field(ge=0, le=2**53 - 1)
    deliveries_cancelled: int = Field(ge=0, le=2**53 - 1)


class SummaryBacklog(Strict):
    module_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    pending: int = Field(ge=0, le=2**53 - 1)
    leased: int = Field(ge=0, le=2**53 - 1)


class Summary(Strict):
    version: Literal["1.0"]
    config_revision: int = Field(ge=0, le=2**53 - 1)
    counts: SummaryCounts
    objects: list[SummaryObject] = Field(max_length=100)
    approvals: list[SummaryApproval] = Field(max_length=100)
    backlogs: list[SummaryBacklog] = Field(max_length=100)


def validate_summary(value):
    """The main API independently validates the collector's bounded public view."""
    return Summary.model_validate(value).model_dump()


class PublicModule(Strict):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    enabled: bool
    machines: list[Machine] = Field(max_length=50)
    emits: list[Literal["state.changed", "publication.requested", "resource.published", "resource.withdrawn"]] = Field(max_length=4)
    accepts: list[Accept] = Field(max_length=200)


class ControlConfiguration(Strict):
    version: Literal["1.0"]
    config_revision: int = Field(ge=0, le=2**53 - 1)
    modules: list[PublicModule] = Field(max_length=100)
    subscriptions: list[Subscription] = Field(max_length=500)

    @model_validator(mode="after")
    def relationships(self):
        Config.model_validate({"expected_revision": self.config_revision,
                               "modules": [{**m.model_dump(), "token": "validation-placeholder-" + m.id} for m in self.modules],
                               "subscriptions": [s.model_dump() for s in self.subscriptions]})
        return self


def validate_configuration(value):
    """Expose metadata needed to recover control writes; tokens are never returned."""
    return ControlConfiguration.model_validate(value).model_dump()
