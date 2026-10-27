"""Registered, scoped module permissions. All decision facts must come from trusted repositories.

Control-plane callers authenticate managers before register/grant/revoke. Decision methods
are read-only, so they can safely participate in a capability's existing DB transaction.
The fixed Casbin model is not supplied by modules, and no legacy-role fallback lives here.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time

import casbin
from jsonschema import Draft202012Validator
from sqlalchemy import Boolean, Float, Integer, JSON, String, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now, uid
from .errors import PlatformError, require


CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "access" / "v1"
DEFINITION_SCHEMA = json.loads(
    (CONTRACT_ROOT / "definition.schema.json").read_text(encoding="utf-8")
)
BINDING_SCHEMA = json.loads((CONTRACT_ROOT / "binding.schema.json").read_text(encoding="utf-8"))


class AccessDefinition(Base):
    __tablename__ = "access_definitions"
    module_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    definition_revision: Mapped[int] = mapped_column(Integer, default=1)
    policy_revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class AccessGrant(Base):
    __tablename__ = "access_grants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    subject_id: Mapped[str] = mapped_column(String(160), index=True)
    module_id: Mapped[str] = mapped_column(String(80), index=True)
    role_id: Mapped[str] = mapped_column(String(160))
    scope: Mapped[dict] = mapped_column(JSON)
    permissions: Mapped[list] = mapped_column(JSON)
    definition_revision: Mapped[int] = mapped_column(Integer)
    # Pin required scope too: a later role edit must not broaden an existing binding.
    required_scope: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[str] = mapped_column(String(50), default=now)
    updated_at: Mapped[str] = mapped_column(String(50), default=now)


class AccessAudit(Base):
    __tablename__ = "access_audit"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(80))
    module_id: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[str] = mapped_column(String(160))
    policy_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(50), default=now)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _validate(schema, value):
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
    require(not errors, "access_contract_invalid", "权限契约不符合已注册版本", 422)


def validate_definition(definition):
    _validate(DEFINITION_SCHEMA, definition)
    prefix = definition["module_id"] + "."
    for section in ("permissions", "role_templates", "experiences", "workitem_sources"):
        ids = [item["id"] for item in definition[section]]
        require(len(set(ids)) == len(ids), "access_duplicate_id", "权限定义含重复标识", 422)
        require(
            all(item.startswith(prefix) for item in ids),
            "access_namespace",
            "权限定义不得越过模块命名空间",
            422,
        )
    permissions = {item["id"] for item in definition["permissions"]}
    for role in definition["role_templates"]:
        require(
            set(role["permissions"]) <= permissions,
            "access_unknown_permission",
            "角色引用未声明权限",
            422,
        )
        require(
            set(role["required_scope"]) <= set(definition["scope_keys"]),
            "access_unknown_scope",
            "角色引用未声明范围",
            422,
        )
    seen = set()
    for mapping in definition["command_mappings"]:
        require(
            mapping["capability_id"].startswith(prefix),
            "access_namespace",
            "不能映射其他模块的能力",
            422,
        )
        require(
            mapping["permission"] in permissions,
            "access_unknown_permission",
            "动作引用未声明权限",
            422,
        )
        key = (mapping["capability_id"], mapping.get("command"))
        require(key not in seen, "access_duplicate_mapping", "能力命令映射重复", 422)
        seen.add(key)
    # A catch-all mapping and command mappings on one capability would make an
    # unknown command accidentally inherit the broad catch-all permission.
    for capability, command in seen:
        if command is None:
            require(
                not any(capability == c and cmd is not None for c, cmd in seen),
                "access_ambiguous_mapping",
                "通用能力映射不能和命令映射混合",
                422,
            )
    for experience in definition["experiences"]:
        require(
            set(experience["required_permissions"]) <= permissions,
            "access_unknown_permission",
            "办理视图引用未声明权限",
            422,
        )
    experiences = {item["id"] for item in definition["experiences"]}
    capabilities = {item["capability_id"] for item in definition["command_mappings"]}
    for source in definition["workitem_sources"]:
        require(
            source["experience_id"] in experiences,
            "access_unknown_experience",
            "待办源引用未声明视图",
            422,
        )
        require(
            source["capability_id"] in capabilities,
            "access_unknown_capability",
            "待办源引用未映射能力",
            422,
        )


class CasbinDecisionEngine:
    """Each intact grant is a separate RBAC domain; scopes never get flattened."""

    name = "casbin-1.43.0"
    MODEL = """[request_definition]
r = sub, dom, act
[policy_definition]
p = sub, dom, act
[role_definition]
g = _, _, _
[policy_effect]
e = some(where (p.eft == allow))
[matchers]
m = g(r.sub, p.sub, r.dom) && r.dom == p.dom && r.act == p.act
"""

    def allowed_grants(self, subject_id, permission, grants):
        # This fixed RBAC model isolates every grant in its own domain. A domain
        # with no policy for this action cannot contribute to authorization.
        grants = [grant for grant in grants if permission in grant["permissions"]]
        if not grants:
            return []
        model = casbin.Model()
        model.load_model_from_text(self.MODEL)
        enforcer = casbin.Enforcer(model)
        for grant in grants:
            domain = grant["id"]
            enforcer.add_grouping_policy(subject_id, grant["role_id"], domain)
            for action in grant["permissions"]:
                enforcer.add_policy(grant["role_id"], domain, action)
        return [g["id"] for g in grants if enforcer.enforce(subject_id, g["id"], permission)]


class AccessControl:
    def __init__(self, sessions, *, engine=None, clock=None):
        self.sessions = sessions
        self.engine = engine or CasbinDecisionEngine()
        self.clock = clock or time.time
        self._borrowed = False

    @classmethod
    def from_session(cls, session, *, engine=None, clock=None):
        result = cls(lambda: nullcontext(session), engine=engine, clock=clock)
        result._borrowed = True
        return result

    def _write_allowed(self):
        require(
            not self._borrowed,
            "access_borrowed_session_read_only",
            "借用业务事务的权限服务仅可读取",
            409,
        )

    @staticmethod
    def _definition(row):
        return {
            **deepcopy(row.definition),
            "definition_revision": row.definition_revision,
            "policy_revision": row.policy_revision,
            "enabled": row.enabled,
            "digest": row.digest,
        }

    @staticmethod
    def _grant(row):
        return {
            key: deepcopy(getattr(row, key))
            for key in (
                "id",
                "subject_id",
                "module_id",
                "role_id",
                "scope",
                "permissions",
                "required_scope",
                "definition_revision",
                "expires_at",
                "revoked",
                "granted_by",
                "created_at",
                "updated_at",
            )
        }

    @staticmethod
    def _audit(db, actor_id, action, module_id, target_id, revision):
        db.add(
            AccessAudit(
                actor_id=actor_id,
                action=action,
                module_id=module_id,
                target_id=target_id,
                policy_revision=revision,
            )
        )

    @staticmethod
    def _advance_policy(db, definition, **values):
        previous = definition.policy_revision
        changed = db.execute(
            update(AccessDefinition)
            .where(
                AccessDefinition.module_id == definition.module_id,
                AccessDefinition.policy_revision == previous,
            )
            .values(policy_revision=previous + 1, updated_at=now(), **values)
        )
        require(changed.rowcount == 1, "access_policy_conflict", "权限策略已更新，请重新读取", 409)
        db.refresh(definition)

    def register_definition(self, definition, *, actor_id, expected_revision=None):
        self._write_allowed()
        validate_definition(definition)
        require(bool(actor_id), "access_manager_required", "缺少定义发布者", 403)
        definition = deepcopy(definition)
        digest = _digest(definition)
        module_id = definition["module_id"]
        with self.sessions() as db:
            row = db.get(AccessDefinition, module_id)
            if row and row.digest == digest:
                return self._definition(row)
            if row:
                require(
                    expected_revision is None or expected_revision == row.policy_revision,
                    "access_policy_conflict",
                    "权限策略已更新，请重新读取",
                    409,
                )
                require(
                    row.definition["version"] != definition["version"],
                    "access_version_reused",
                    "修改权限定义需要递增版本",
                    409,
                )
                previous = row.policy_revision
                result = db.execute(
                    update(AccessDefinition)
                    .where(
                        AccessDefinition.module_id == module_id,
                        AccessDefinition.policy_revision == previous,
                    )
                    .values(
                        definition=definition,
                        digest=digest,
                        definition_revision=row.definition_revision + 1,
                        policy_revision=previous + 1,
                        updated_at=now(),
                    )
                )
                require(
                    result.rowcount == 1,
                    "access_policy_conflict",
                    "权限策略已更新，请重新读取",
                    409,
                )
                db.expire(row)
            else:
                require(
                    expected_revision is None, "access_policy_conflict", "权限定义尚未注册", 409
                )
                existing_modules = db.scalars(select(AccessDefinition.module_id)).all()
                require(
                    not any(
                        module_id.startswith(other + ".") or other.startswith(module_id + ".")
                        for other in existing_modules
                    ),
                    "access_namespace_overlap",
                    "模块命名空间不能相互包含",
                    422,
                )
                row = AccessDefinition(
                    module_id=module_id,
                    definition=definition,
                    digest=digest,
                    definition_revision=1,
                    policy_revision=1,
                    enabled=True,
                )
                db.add(row)
            try:
                db.flush()
                self._audit(
                    db, actor_id, "definition.register", module_id, module_id, row.policy_revision
                )
                db.commit()
            except IntegrityError as error:
                db.rollback()
                raise PlatformError(
                    "access_policy_conflict", "权限定义已被其他请求注册", 409
                ) from error
            return self._definition(row)

    def definitions(self):
        with self.sessions() as db:
            return [
                self._definition(row)
                for row in db.scalars(select(AccessDefinition).order_by(AccessDefinition.module_id))
            ]

    def module_registered(self, module_id):
        with self.sessions() as db:
            return db.get(AccessDefinition, module_id) is not None

    def policy_revision(self, module_id):
        with self.sessions() as db:
            row = db.get(AccessDefinition, module_id)
            return row.policy_revision if row else 0

    def set_enabled(self, module_id, enabled, *, actor_id):
        self._write_allowed()
        require(bool(actor_id), "access_manager_required", "缺少权限操作者", 403)
        require(type(enabled) is bool, "access_contract_invalid", "启停状态必须是布尔值", 422)
        with self.sessions() as db:
            row = db.get(AccessDefinition, module_id)
            require(row is not None, "access_definition_missing", "权限定义不存在", 404)
            if row.enabled != enabled:
                # A disable/enable cycle must not resurrect old authorization stamps.
                self._advance_policy(
                    db, row, enabled=enabled, definition_revision=row.definition_revision + 1
                )
                self._audit(
                    db,
                    actor_id,
                    "definition.enable" if enabled else "definition.disable",
                    module_id,
                    module_id,
                    row.policy_revision,
                )
                db.commit()
            return self._definition(row)

    def grant(
        self,
        subject_id,
        module_id,
        role_id,
        scope,
        *,
        actor_id,
        expires_at=None,
        expected_policy_revision=None,
        _transaction=None,
    ):
        self._write_allowed()
        _validate(
            BINDING_SCHEMA,
            dict(
                subject_id=subject_id,
                module_id=module_id,
                role_id=role_id,
                scope=scope,
                expires_at=expires_at,
                expected_policy_revision=expected_policy_revision,
            ),
        )
        require(bool(actor_id), "access_manager_required", "缺少授权操作者", 403)
        require(
            expires_at is None or (math.isfinite(expires_at) and expires_at > self.clock()),
            "access_expiry_invalid",
            "授权有效期必须在未来",
            422,
        )
        with nullcontext(_transaction) if _transaction is not None else self.sessions() as db:
            definition = db.get(AccessDefinition, module_id)
            require(
                definition is not None and definition.enabled,
                "access_definition_missing",
                "模块权限定义不可用",
                404,
            )
            require(
                expected_policy_revision is None
                or expected_policy_revision == definition.policy_revision,
                "access_policy_conflict",
                "权限策略已更新",
                409,
            )
            role = next(
                (r for r in definition.definition["role_templates"] if r["id"] == role_id), None
            )
            require(role is not None, "access_role_missing", "角色模板不存在", 422)
            require(
                set(role["required_scope"])
                <= set(scope)
                <= set(definition.definition["scope_keys"]),
                "access_scope_invalid",
                "授权必须包含角色所需范围，且不得出现未知范围",
                422,
            )
            grant = AccessGrant(
                subject_id=subject_id,
                module_id=module_id,
                role_id=role_id,
                scope=deepcopy(scope),
                permissions=deepcopy(role["permissions"]),
                required_scope=deepcopy(role["required_scope"]),
                definition_revision=definition.definition_revision,
                expires_at=expires_at,
                revoked=False,
                granted_by=actor_id,
            )
            db.add(grant)
            self._advance_policy(db, definition)
            db.flush()
            self._audit(
                db, actor_id, "grant.create", module_id, grant.id, definition.policy_revision
            )
            if _transaction is None:
                db.commit()
            return self._grant(grant)

    def grant_many(self, subject_id, bindings, *, actor_id, expires_at=None):
        """Apply an explicit role group atomically, preserving each module's scope.

        A retry reuses an identical active grant; similarly named roles from
        different modules are always separate bindings. One invalid binding
        rolls back the entire group, including policy revisions and audit rows.
        """
        from .db import User
        from .identity import has_permission, load_actor

        self._write_allowed()
        require(
            0 < len(bindings) <= 20,
            "access_group_size",
            "一组角色需要包含 1 至 20 项明确授权。",
            422,
        )
        with self.sessions.begin() as db:
            actor = load_actor(db, actor_id)
            require(
                has_permission(actor, "identity.manage"),
                "forbidden",
                "组合授权需要身份管理权限。",
                403,
            )
            subject = db.get(User, subject_id)
            require(
                subject is not None and subject.enabled,
                "access_subject",
                "授权用户不存在或已停用。",
                422,
            )
            result = []
            for binding in bindings:
                definition = db.get(AccessDefinition, binding["module_id"])
                require(
                    definition is not None and definition.enabled,
                    "access_definition_missing",
                    "模块权限定义不可用。",
                    404,
                )
                # Exact scope equality preserves OR-of-AND semantics. Never union
                # separate organization/project scopes into a broader grant.
                existing = next(
                    (
                        g
                        for g in db.query(AccessGrant).filter_by(
                            subject_id=subject_id,
                            module_id=binding["module_id"],
                            role_id=binding["role_id"],
                            revoked=False,
                            definition_revision=definition.definition_revision,
                        )
                        if g.scope == binding["scope"]
                        and g.expires_at == expires_at
                        and (g.expires_at is None or g.expires_at > self.clock())
                    ),
                    None,
                )
                if existing:
                    result.append(self._grant(existing))
                else:
                    result.append(
                        self.grant(
                            subject_id,
                            **binding,
                            actor_id=actor_id,
                            expires_at=expires_at,
                            _transaction=db,
                        )
                    )
            return {"items": result}

    def revoke(self, grant_id, *, actor_id):
        self._write_allowed()
        require(bool(actor_id), "access_manager_required", "缺少撤权操作者", 403)
        with self.sessions() as db:
            grant = db.get(AccessGrant, grant_id)
            require(grant is not None, "access_grant_missing", "授权记录不存在", 404)
            if not grant.revoked:
                grant.revoked = True
                grant.updated_at = now()
                definition = db.get(AccessDefinition, grant.module_id)
                self._advance_policy(db, definition)
                self._audit(
                    db,
                    actor_id,
                    "grant.revoke",
                    grant.module_id,
                    grant.id,
                    definition.policy_revision,
                )
                db.commit()
            return self._grant(grant)

    def bindings(self, subject_id=None, module_id=None):
        with self.sessions() as db:
            query = select(AccessGrant).order_by(AccessGrant.created_at, AccessGrant.id)
            if subject_id is not None:
                query = query.where(AccessGrant.subject_id == subject_id)
            if module_id is not None:
                query = query.where(AccessGrant.module_id == module_id)
            return [self._grant(row) for row in db.scalars(query)]

    def _context(self, actor):
        with self.sessions() as db:
            by_module = {
                row.module_id: self._definition(row) for row in db.scalars(select(AccessDefinition))
            }
            if not isinstance(actor, dict) or not actor.get("id") or actor.get("enabled") is False:
                return by_module, []
            bindings = [
                self._grant(row)
                for row in db.scalars(
                    select(AccessGrant).where(AccessGrant.subject_id == actor["id"])
                )
            ]
        grants = []
        for binding in bindings:
            definition = by_module.get(binding["module_id"])
            if binding["revoked"] or not definition or not definition["enabled"]:
                continue
            if binding["expires_at"] is not None and binding["expires_at"] <= self.clock():
                continue
            role = next(
                (r for r in definition["role_templates"] if r["id"] == binding["role_id"]), None
            )
            if role is None or not set(role["required_scope"]) <= set(binding["scope"]) <= set(
                definition["scope_keys"]
            ):
                continue
            binding["permissions"] = sorted(set(binding["permissions"]) & set(role["permissions"]))
            if binding["permissions"]:
                grants.append(binding)
        return by_module, grants

    @staticmethod
    def _scope_matches(scope, facts):
        if not isinstance(facts, dict):
            return False
        for key, value in scope.items():
            actual = facts.get(key)
            if isinstance(actual, str):
                if value != actual:
                    return False
            elif (
                isinstance(actual, list)
                and actual
                and all(isinstance(item, str) and item for item in actual)
            ):
                if value not in actual:
                    return False
            else:
                return False
        return True

    def has_permission(self, actor, permission):
        """Catalog visibility only. Object access must use check with fresh trusted facts."""
        _, grants = self._context(actor)
        if not grants:
            return False
        # Catalog visibility needs one matching intact domain, not every grant
        # ID. Keep current repository reads and use Casbin on each candidate;
        # do not cache a prior allow across expiry, revocation or role changes.
        return any(
            self.engine.allowed_grants(actor["id"], permission, [grant])
            for grant in grants if permission in grant["permissions"]
        )

    def matching_scopes(self, actor, permission):
        """Return intact OR-of-AND scopes to a trusted query adapter, never flatten them."""
        _, grants = self._context(actor)
        allowed = (
            set(self.engine.allowed_grants(actor["id"], permission, grants)) if grants else set()
        )
        return [deepcopy(g["scope"]) for g in grants if g["id"] in allowed]

    def check(self, actor, permission, facts, *, command=None, expected_policy_revision=None):
        """结合当前授权和可信对象事实判断权限；返回明确的允许或拒绝结果。"""
        by_module, grants = self._context(actor)
        definition = next(
            (d for d in by_module.values() if any(p["id"] == permission for p in d["permissions"])),
            None,
        )
        result = dict(
            allowed=False,
            reason="permission_unknown",
            module_id=definition["module_id"] if definition else None,
            policy_revision=definition["policy_revision"] if definition else 0,
            grant_ids=[],
            engine=self.engine.name,
        )
        if not definition:
            return result
        if not isinstance(actor, dict) or not actor.get("id") or actor.get("enabled") is False:
            return {**result, "reason": "identity_inactive"}
        if not definition["enabled"]:
            return {**result, "reason": "module_disabled"}
        if (
            expected_policy_revision is not None
            and expected_policy_revision != definition["policy_revision"]
        ):
            return {**result, "reason": "policy_revision_changed"}
        permission_definition = next(p for p in definition["permissions"] if p["id"] == permission)
        for guard in permission_definition.get("guards", []):
            actual = facts.get(guard["fact"]) if isinstance(facts, dict) else None
            if not isinstance(actual, str) or not actual:
                return {**result, "reason": "required_fact_missing"}
            if guard["type"] == "actor_not_fact" and actual == actor["id"]:
                return {**result, "reason": "separation_of_duties"}
            if guard["type"] == "actor_is_fact" and actual != actor["id"]:
                return {**result, "reason": "actor_relation_mismatch"}
            if guard["type"] == "fact_in" and actual not in guard["values"]:
                return {**result, "reason": "fact_condition_failed"}
        candidates = [
            g
            for g in grants
            if g["module_id"] == definition["module_id"] and self._scope_matches(g["scope"], facts)
        ]
        allowed = self.engine.allowed_grants(actor["id"], permission, candidates)
        return {
            **result,
            "allowed": bool(allowed),
            "reason": "allowed" if allowed else "grant_scope_mismatch",
            "grant_ids": allowed,
        }

    def capability_permission(self, capability_id, command=None):
        for definition in self.definitions():
            for item in definition["command_mappings"]:
                if item["capability_id"] == capability_id and item.get("command") == command:
                    return item["permission"]
        return None

    def capability_permissions(self, capability_id):
        return sorted(
            {
                mapping["permission"]
                for definition in self.definitions()
                for mapping in definition["command_mappings"]
                if mapping["capability_id"] == capability_id
            }
        )

    def check_command(self, actor, capability_id, payload, facts, *, expected_policy_revision=None):
        """将公开能力映射到权限，再用业务仓储提供的事实检查对象范围。"""
        command = payload.get("command") if isinstance(payload, dict) else None
        # workflow v1 uses a structured `command` envelope. It is not a string
        # selector. Such capabilities need their own unqualified mapping; never
        # use this branch to fall back from an unknown string sub-command.
        if isinstance(command, dict):
            command = None
        permission = self.capability_permission(capability_id, command)
        # An explicit unknown command is never converted to an unqualified action.
        if permission is None:
            return dict(
                allowed=False,
                reason="command_unmapped",
                module_id=None,
                policy_revision=0,
                grant_ids=[],
                engine=self.engine.name,
            )
        return self.check(
            actor,
            permission,
            facts,
            command=command,
            expected_policy_revision=expected_policy_revision,
        )

    def experiences(self, actor, module_id=None, facts=None):
        result = []
        for definition in self.definitions():
            if not definition["enabled"] or (
                module_id is not None and definition["module_id"] != module_id
            ):
                continue
            for experience in definition["experiences"]:
                allowed = all(
                    (
                        self.has_permission(actor, p)
                        if facts is None
                        else self.check(actor, p, facts)["allowed"]
                    )
                    for p in experience["required_permissions"]
                )
                if allowed:
                    result.append(
                        {
                            **deepcopy(experience),
                            "module_id": definition["module_id"],
                            "policy_revision": definition["policy_revision"],
                        }
                    )
        return result

    def authorization_stamp(self, actor):
        definitions, grants = self._context(actor)
        return _digest(
            {
                "subject": actor.get("id") if isinstance(actor, dict) else None,
                # Other users' grant/revoke events must not invalidate this
                # user's flow. Definition changes and own grants still do.
                "definition_revisions": sorted(
                    (d["module_id"], d["definition_revision"], d["enabled"])
                    for d in definitions.values()
                ),
                "grants": sorted(
                    (
                        g["id"],
                        g["definition_revision"],
                        g["permissions"],
                        g["scope"],
                        g["expires_at"],
                    )
                    for g in grants
                ),
            }
        )
