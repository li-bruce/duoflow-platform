from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    identity_profile: Mapped["IdentityProfile | None"] = relationship(
        lazy="selectin", uselist=False
    )
    team_memberships: Mapped[list["TeamMembership"]] = relationship(lazy="selectin")


class Organization(Base):
    __tablename__ = "identity_organizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(30), default="other")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class Team(Base):
    __tablename__ = "identity_teams"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("identity_organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class IdentityProfile(Base):
    __tablename__ = "identity_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("identity_organizations.id"), nullable=True
    )
    authorization_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class TeamMembership(Base):
    __tablename__ = "identity_team_memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("identity_teams.id"), primary_key=True)


class IdentityBootstrap(Base):
    __tablename__ = "identity_bootstrap"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    csrf: Mapped[str] = mapped_column(String(100))
    expires: Mapped[float] = mapped_column(Float)


class Invocation(Base):
    __tablename__ = "invocations"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    capability_id: Mapped[str] = mapped_column(String(160))
    capability_version: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    principal_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(30))
    conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    invocation_id: Mapped[str] = mapped_column(String(40), unique=True)
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    capability_id: Mapped[str] = mapped_column(String(160))
    capability_version: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)
    input: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class Resource(Base):
    __tablename__ = "resources"
    access_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    workspace_id: Mapped[str] = mapped_column(String(80), default="local")
    type: Mapped[str] = mapped_column(String(150))
    title: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    authorization_revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class ResourceVersion(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (UniqueConstraint("resource_id", "revision"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    resource_id: Mapped[str] = mapped_column(String(40), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    resource_id: Mapped[str] = mapped_column(String(40), index=True)
    owner_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fork_message_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class UserConversationState(Base):
    __tablename__ = "user_conversation_states"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    current_conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    drafts: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "request_key", "role"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    views: Mapped[list] = mapped_column(JSON, default=list)
    request_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    access_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


class InteractionInstance(Base):
    __tablename__ = "interaction_instances"
    __table_args__ = (UniqueConstraint("owner_id", "creation_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    authorization_revision: Mapped[int] = mapped_column(Integer)
    access_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    component_id: Mapped[str] = mapped_column(String(120), index=True)
    component_version: Mapped[str] = mapped_column(String(30))
    definition_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_action: Mapped[str | None] = mapped_column(String(61), nullable=True)
    creation_key: Mapped[str] = mapped_column(String(100))
    creation_hash: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    forked_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="editing")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class WorkflowInstance(Base):
    """Private UI navigation and drafts; never an authoritative business record."""

    __tablename__ = "workflow_instances"
    __table_args__ = (UniqueConstraint("owner_id", "creation_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    authorization_revision: Mapped[int] = mapped_column(Integer)
    access_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flow_id: Mapped[str] = mapped_column(String(120), index=True)
    flow_version: Mapped[str] = mapped_column(String(30))
    definition_hash: Mapped[str] = mapped_column(String(64))
    creation_key: Mapped[str] = mapped_column(String(100))
    creation_hash: Mapped[str] = mapped_column(String(64))
    object_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cursor: Mapped[str] = mapped_column(String(80))
    drafts: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    forked_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_invocation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class WorkflowObservation(Base):
    """Bounded host observations, never command payloads or business event logs."""

    __tablename__ = "workflow_observations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    instance_id: Mapped[str] = mapped_column(String(40), index=True)
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    object_key: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    observed: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[str] = mapped_column(String(50), default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(160))
    target: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


def create_database(data_dir: Path, database_url: str | None = None):
    data_dir.mkdir(parents=True, exist_ok=True)
    url = (
        database_url
        or os.getenv("DATABASE_URL")
        or "sqlite:///" + (data_dir / "platform.db").as_posix()
    )
    engine = create_engine(
        url,
        connect_args=(
            {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        ),
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")

    Base.metadata.create_all(engine)
    # Existing installations predate these optional conversation fields. New
    # identity tables are additive; preserve all existing rows and passwords.
    with engine.begin() as connection:
        for table in ("messages", "interaction_instances", "resources"):
            if "access_stamp" not in {c["name"] for c in inspect(connection).get_columns(table)}:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN access_stamp VARCHAR(64)"))
        workflow_columns = {
            column["name"] for column in inspect(connection).get_columns("workflow_instances")
        }
        if "access_stamp" not in workflow_columns:
            connection.execute(
                text("ALTER TABLE workflow_instances ADD COLUMN access_stamp VARCHAR(64)")
            )
        interaction_columns = {
            column["name"] for column in inspect(connection).get_columns("interaction_instances")
        }
        for name, length in (("run_id", 40), ("active_action", 61)):
            if name not in interaction_columns:
                connection.execute(
                    text(f"ALTER TABLE interaction_instances ADD COLUMN {name} VARCHAR({length})")
                )
        invocation_columns = {
            column["name"] for column in inspect(connection).get_columns("invocations")
        }
        if "principal_snapshot" not in invocation_columns:
            connection.execute(
                text("ALTER TABLE invocations ADD COLUMN principal_snapshot JSON DEFAULT '{}'")
            )
        resource_columns = {
            column["name"] for column in inspect(connection).get_columns("resources")
        }
        if "authorization_revision" not in resource_columns:
            connection.execute(
                text("ALTER TABLE resources ADD COLUMN authorization_revision INTEGER DEFAULT 0")
            )
        columns = {column["name"] for column in inspect(connection).get_columns("conversations")}
        if "fork_message_id" not in columns:
            connection.execute(
                text("ALTER TABLE conversations ADD COLUMN fork_message_id VARCHAR(40)")
            )
        if "updated_at" not in columns:
            connection.execute(text("ALTER TABLE conversations ADD COLUMN updated_at VARCHAR(50)"))
        connection.execute(
            text("UPDATE conversations SET updated_at = created_at WHERE updated_at IS NULL")
        )
    return engine, sessionmaker(engine, expire_on_commit=False)


def user_dict(user: User):
    from .identity import claims_for_user

    return claims_for_user(user)


def audit(session, actor_id: str, action: str, target: str, **detail):
    session.add(Audit(actor_id=actor_id, action=action, target=target, detail=detail))


def resource_dict(session, resource: Resource):
    versions = (
        session.query(ResourceVersion)
        .filter_by(resource_id=resource.id)
        .order_by(ResourceVersion.revision.desc())
        .all()
    )
    artifacts = session.query(Artifact).filter_by(resource_id=resource.id).all()
    return {
        **{
            k: getattr(resource, k)
            for k in (
                "id",
                "type",
                "title",
                "revision",
                "status",
                "data",
                "created_at",
                "updated_at",
            )
        },
        "versions": [
            {
                "revision": v.revision,
                "status": v.status,
                "note": v.note,
                "created_at": v.created_at,
                "data": v.data,
            }
            for v in versions
        ],
        "artifacts": [
            {"id": a.id, "name": a.name, "media_type": a.media_type, "sha256": a.sha256}
            for a in artifacts
        ],
    }


def run_dict(run: Run):
    return {
        k: getattr(run, k)
        for k in (
            "id",
            "capability_id",
            "status",
            "input",
            "result",
            "error",
            "created_at",
            "updated_at",
            "attempt",
            "progress",
        )
    }
