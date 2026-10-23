from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .db import Artifact, Resource, ResourceVersion, resource_dict


@dataclass
class CapabilityOutput:
    summary: str
    data: dict
    views: list = field(default_factory=list)
    resource: dict | None = None
    artifacts: list = field(default_factory=list)


@dataclass
class CapabilityContext:
    session: Any
    actor: dict
    workspace_id: str
    gateway: Any
    run_id: str | None = None
    invocation_id: str | None = None
    origin: str = "page"
    access: Any = None
    access_stamp: str | None = None

    def create_resource(self, type: str, title: str, data: dict) -> dict:
        resource = Resource(
            owner_id=self.actor["id"],
            workspace_id=self.workspace_id,
            type=type,
            title=title[:200],
            data=data,
            authorization_revision=self.actor.get("authorization_revision", 0),
            access_stamp=self.access_stamp,
        )
        self.session.add(resource)
        self.session.flush()
        self.session.add(
            ResourceVersion(
                resource_id=resource.id,
                revision=1,
                data=data,
                status="draft",
                note="创建",
                actor_id=self.actor["id"],
            )
        )
        self.session.flush()
        return resource_dict(self.session, resource)

    def create_artifact(
        self, resource_id: str, name: str, content: str | bytes, media_type: str
    ) -> dict:
        from .errors import require

        resource = self.session.get(Resource, resource_id)
        require(
            resource is not None and resource.owner_id == self.actor["id"],
            "artifact_scope",
            "产物需要关联当前用户的对象。",
            403,
        )
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        require(
            len(content.encode("utf-8")) <= 2_000_000, "artifact_size", "本地演示产物最大 2 MB。"
        )
        safe_name = (
            name.replace("/", "_").replace("\\", "_").replace("\r", "").replace("\n", "")[:180]
        )
        artifact = Artifact(
            resource_id=resource_id,
            owner_id=self.actor["id"],
            name=safe_name,
            media_type=media_type,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        self.session.add(artifact)
        self.session.flush()
        return {
            "id": artifact.id,
            "name": artifact.name,
            "media_type": media_type,
            "sha256": artifact.sha256,
        }
