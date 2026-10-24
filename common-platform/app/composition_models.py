"""Separate persistence for composition definitions, attempts and step receipts."""

from sqlalchemy import Boolean, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now, uid


class CompositionDefinition(Base):
    __tablename__ = "composition_definitions"
    __table_args__ = (UniqueConstraint("owner_id", "logical_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    logical_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(30))
    document: Mapped[dict] = mapped_column(JSON)
    pins: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class CompositionRun(Base):
    __tablename__ = "composition_runs"
    __table_args__ = (UniqueConstraint("owner_id", "creation_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    definition_id: Mapped[str] = mapped_column(String(40), index=True)
    principal: Mapped[dict] = mapped_column(JSON)
    creation_key: Mapped[str] = mapped_column(String(100))
    creation_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class CompositionStep(Base):
    __tablename__ = "composition_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    step_id: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    invocation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    instance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dispatch_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)
