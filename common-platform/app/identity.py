"""Central identity and role policy; OAuth/OIDC remains an external login option.

The scalar role is a display/primary role. Authorization always uses the complete
effective role set. A system administrator has no implicit business read-all.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import secrets

from sqlalchemy import delete, select, text

from .db import (
    IdentityBootstrap,
    IdentityProfile,
    LoginSession,
    Organization,
    Team,
    TeamMembership,
    User,
    audit,
    now,
    uid,
)
from .errors import require

POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "identity-policy.json"


@lru_cache(maxsize=1)
def identity_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def effective_roles(actor):
    """Return known assigned roles plus declared implications; never trust permissions input."""
    catalog = identity_policy()["roles"]
    assigned = actor.get("roles")
    roles = set(
        assigned if isinstance(assigned, (list, tuple, set)) and assigned else [actor.get("role")]
    ) & set(catalog)
    pending = list(roles)
    while pending:
        for implied in catalog[pending.pop()]["implies"]:
            if implied not in roles:
                roles.add(implied)
                pending.append(implied)
    return roles


def has_role(actor, role_or_roles):
    """True when any requested role is effective (any-of, not all-of)."""
    wanted = {role_or_roles} if isinstance(role_or_roles, str) else set(role_or_roles)
    return bool(effective_roles(actor) & wanted)


def permissions_for(actor):
    catalog = identity_policy()["roles"]
    return sorted(
        {
            permission
            for role in effective_roles(actor)
            for permission in catalog[role]["permissions"]
        }
    )


def has_permission(actor, permission):
    return permission in permissions_for(actor)


def can_manage_platform(actor):
    return has_role(actor, {"admin", "system_admin"})


def capability_allowed(actor, spec):
    """Capability roles are any-of; availability and object scope remain separate gates."""
    return bool(effective_roles(actor) & set(spec.get("roles", [])))


def claims_for_user(user):
    profile = user.identity_profile
    assigned = list(profile.roles) if profile and profile.roles else [user.role]
    # Legacy code may update User.role directly. A conflicting profile must not
    # retain a more privileged role after that scalar authority was revoked.
    if user.role not in assigned:
        assigned = [user.role]
    actor = {key: getattr(user, key) for key in ("id", "username", "name", "role")}
    actor.update(
        roles=sorted(set(assigned)),
        organization_id=profile.organization_id if profile else None,
        team_ids=sorted(m.team_id for m in user.team_memberships),
        authorization_revision=profile.authorization_revision if profile else 1,
    )
    actor["roles"] = sorted(effective_roles(actor))
    actor["permissions"] = permissions_for(actor)
    return actor


def authorization_snapshot(actor):
    """Non-secret immutable claims suitable for invocation records and audit."""
    return {
        key: actor.get(key)
        for key in ("id", "role", "roles", "organization_id", "team_ids", "authorization_revision")
    }


def load_actor(db, user_id):
    """Reload an enabled user and current claims from this transaction's database."""
    db.expire_all()
    user = db.get(User, user_id)
    require(user is not None and user.enabled, "actor_disabled", "账号已停用或不存在。", 403)
    return claims_for_user(user)


def refresh_actor(db, actor, *, require_unchanged=False):
    current = load_actor(db, actor["id"])
    if require_unchanged:
        require(
            authorization_snapshot(current) == authorization_snapshot(actor),
            "authorization_changed",
            "权限或所属范围已变更，请重新发起操作。",
            403,
        )
    return current


def scope_allowed(
    actor, owner_id, *, scope="private", organization_id=None, team_id=None, permission=None
):
    """Explicit private/team/organization scope; same organization alone never shares data."""
    if permission and not has_permission(actor, permission):
        return False
    if has_role(actor, "admin") or owner_id == actor.get("id"):
        return True
    if scope == "team":
        return bool(
            team_id
            and team_id in actor.get("team_ids", [])
            and organization_id == actor.get("organization_id")
        )
    if scope == "organization":
        return bool(organization_id and organization_id == actor.get("organization_id"))
    return False


@contextmanager
def identity_transaction(sessions):
    # Serialize last-administrator and membership invariants on SQLite.
    with sessions() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _public(user):
    return {**claims_for_user(user), "enabled": user.enabled}


def _profile(db, user):
    if user.identity_profile is None:
        user.identity_profile = IdentityProfile(
            user_id=user.id, roles=[user.role], authorization_revision=1
        )
        db.add(user.identity_profile)
        db.flush()
    return user.identity_profile


def _roles(role, roles):
    catalog = identity_policy()["roles"]
    require(isinstance(role, str) and role in catalog, "identity_role", "主角色无效。", 422)
    require(
        isinstance(roles, list)
        and roles
        and all(isinstance(item, str) and item in catalog for item in roles),
        "identity_roles",
        "角色列表无效。",
        422,
    )
    require(
        role in roles and len(set(roles)) == len(roles),
        "identity_roles",
        "主角色必须包含在不重复的角色列表中。",
        422,
    )
    return sorted(roles)


def _memberships(db, organization_id, team_ids):
    require(
        isinstance(team_ids, list)
        and len(team_ids) <= 100
        and all(isinstance(item, str) for item in team_ids)
        and len(set(team_ids)) == len(team_ids),
        "identity_teams",
        "团队列表无效。",
        422,
    )
    if organization_id is not None:
        org = db.get(Organization, organization_id)
        require(org is not None and org.enabled, "identity_organization", "所属机构不可用。", 422)
    for team_id in team_ids:
        team = db.get(Team, team_id)
        require(
            team is not None and team.enabled and team.organization_id == organization_id,
            "identity_team_scope",
            "团队必须属于账号的当前机构。",
            422,
        )
    return sorted(team_ids)


def _password(value):
    require(
        isinstance(value, str) and 12 <= len(value) <= 200,
        "identity_password",
        "密码长度应为 12 至 200 个字符。",
        422,
    )
    return value


def _manager(db, actor):
    current = refresh_actor(db, actor, require_unchanged=True)
    require(can_manage_platform(current), "forbidden", "该操作需要系统管理员身份。", 403)
    return current


class IdentityService:
    def __init__(self, sessions):
        self.sessions = sessions

    def catalog(self, actor):
        with self.sessions() as db:
            _manager(db, actor)
            return {
                "roles": [{"id": key, **value} for key, value in identity_policy()["roles"].items()]
            }

    def list_users(self, actor):
        with self.sessions() as db:
            _manager(db, actor)
            return {
                "items": [
                    _public(user) for user in db.scalars(select(User).order_by(User.username))
                ]
            }

    def list_organizations(self, actor):
        with self.sessions() as db:
            _manager(db, actor)
            return {
                "items": [
                    {key: getattr(org, key) for key in ("id", "name", "kind", "enabled")}
                    for org in db.scalars(select(Organization).order_by(Organization.name))
                ]
            }

    def list_teams(self, actor):
        with self.sessions() as db:
            _manager(db, actor)
            return {
                "items": [
                    {
                        key: getattr(team, key)
                        for key in ("id", "name", "organization_id", "enabled")
                    }
                    for team in db.scalars(select(Team).order_by(Team.name))
                ]
            }

    def create_organization(self, actor, payload):
        with identity_transaction(self.sessions) as db:
            current = _manager(db, actor)
            require(
                not db.scalar(select(Organization).where(Organization.name == payload["name"])),
                "identity_duplicate",
                "机构名称已存在。",
                409,
            )
            org = Organization(name=payload["name"], kind=payload["kind"])
            db.add(org)
            db.flush()
            result = {key: getattr(org, key) for key in ("id", "name", "kind", "enabled")}
            audit(db, current["id"], "identity.organization.create", org.id, after=result)
            return result

    def create_team(self, actor, payload):
        with identity_transaction(self.sessions) as db:
            current = _manager(db, actor)
            _memberships(db, payload["organization_id"], [])
            require(
                not db.scalar(
                    select(Team).where(
                        Team.organization_id == payload["organization_id"],
                        Team.name == payload["name"],
                    )
                ),
                "identity_duplicate",
                "该机构内已存在同名团队。",
                409,
            )
            team = Team(name=payload["name"], organization_id=payload["organization_id"])
            db.add(team)
            db.flush()
            result = {
                key: getattr(team, key) for key in ("id", "name", "organization_id", "enabled")
            }
            audit(db, current["id"], "identity.team.create", team.id, after=result)
            return result

    def create_user(self, actor, payload):
        from .security import hash_password

        password = _password(payload.get("password") or secrets.token_urlsafe(24))
        roles = _roles(payload["role"], payload["roles"])
        require(
            "admin" not in roles,
            "identity_legacy_role",
            "旧演示管理员角色不能分配给新账号，请使用系统管理员角色。",
            422,
        )
        require(
            bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}", payload["username"])),
            "identity_username",
            "账号名应为 3 至 80 个字母、数字或 ._@- 字符。",
            422,
        )
        with identity_transaction(self.sessions) as db:
            current = _manager(db, actor)
            require(
                not db.scalar(select(User).where(User.username == payload["username"])),
                "identity_duplicate",
                "账号名已存在。",
                409,
            )
            team_ids = _memberships(db, payload.get("organization_id"), payload.get("team_ids", []))
            user = User(
                username=payload["username"],
                name=payload["name"],
                role=payload["role"],
                password_hash=hash_password(password),
                enabled=True,
            )
            db.add(user)
            db.flush()
            user.identity_profile = IdentityProfile(
                user_id=user.id,
                roles=roles,
                organization_id=payload.get("organization_id"),
                authorization_revision=1,
            )
            user.team_memberships = [
                TeamMembership(user_id=user.id, team_id=team_id) for team_id in team_ids
            ]
            db.flush()
            result = _public(user)
            audit(db, current["id"], "identity.user.create", user.id, after=result)
            return {"user": result, "initial_password": password}

    def update_user(self, actor, user_id, payload):
        with identity_transaction(self.sessions) as db:
            current = _manager(db, actor)
            user = db.get(User, user_id)
            require(user is not None, "identity_missing", "账号不存在。", 404)
            profile = _profile(db, user)
            require(
                profile.authorization_revision == payload["expected_revision"],
                "revision_conflict",
                "账号信息已更新，请刷新后重试。",
                409,
            )
            before = _public(user)
            role = payload.get("role", user.role)
            roles = _roles(role, payload.get("roles", profile.roles))
            require(
                "admin" not in roles or has_role(before, "admin"),
                "identity_legacy_role",
                "旧演示管理员角色只能保留或移除，不能新增分配。",
                422,
            )
            organization_id = payload.get("organization_id", profile.organization_id)
            team_ids = _memberships(
                db, organization_id, payload.get("team_ids", before["team_ids"])
            )
            enabled = payload.get("enabled", user.enabled)
            proposed = {
                **before,
                "role": role,
                "roles": sorted(effective_roles({"role": role, "roles": roles})),
                "organization_id": organization_id,
                "team_ids": team_ids,
                "enabled": enabled,
            }
            if (
                user.enabled
                and can_manage_platform(before)
                and not (enabled and can_manage_platform(proposed))
            ):
                others = [
                    candidate
                    for candidate in db.scalars(
                        select(User).where(User.enabled.is_(True), User.id != user.id)
                    )
                    if can_manage_platform(claims_for_user(candidate))
                ]
                require(
                    bool(others), "identity_last_admin", "请保留至少一个可用的系统管理员账号。", 409
                )
            user.name = payload.get("name", user.name)
            user.role, user.enabled = role, enabled
            profile.roles, profile.organization_id = roles, organization_id
            security_changed = any(
                before[key] != proposed[key]
                for key in ("role", "roles", "organization_id", "team_ids", "enabled")
            )
            if security_changed:
                db.execute(delete(TeamMembership).where(TeamMembership.user_id == user.id))
                for team_id in team_ids:
                    db.add(TeamMembership(user_id=user.id, team_id=team_id))
                profile.authorization_revision += 1
                profile.updated_at = now()
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                db.flush()
                db.expire(user, ["team_memberships"])
            result = _public(user)
            audit(
                db,
                current["id"],
                "identity.user.update",
                user.id,
                before=before,
                after=result,
                sessions_revoked=security_changed,
            )
            return result

    def reset_password(self, actor, user_id, payload):
        from .security import hash_password

        password = _password(payload.get("password") or secrets.token_urlsafe(24))
        with identity_transaction(self.sessions) as db:
            current = _manager(db, actor)
            user = db.get(User, user_id)
            require(user is not None, "identity_missing", "账号不存在。", 404)
            profile = _profile(db, user)
            require(
                profile.authorization_revision == payload["expected_revision"],
                "revision_conflict",
                "账号信息已更新，请刷新后重试。",
                409,
            )
            user.password_hash = hash_password(password)
            profile.authorization_revision += 1
            profile.updated_at = now()
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            result = _public(user)
            audit(
                db,
                current["id"],
                "identity.user.password_reset",
                user.id,
                authorization_revision=profile.authorization_revision,
                sessions_revoked=True,
            )
            return {"user": result, "initial_password": password}


def bootstrap_identity(sessions, data_dir=None, *, demo_passwords=None, include_demo_accounts=True):
    """One-time local demo bootstrap. Existing accounts/passwords are never replaced."""
    from .security import hash_password

    passwords = demo_passwords or {}
    credentials = []
    with identity_transaction(sessions) as db:
        if db.get(IdentityBootstrap, "identity-v04") is not None:
            return
        for username, name, role, password in [
            ("admin", "旧版演示管理员", "admin", "demo-admin-2026"),
            ("member", "演示使用者", "member", "demo-member-2026"),
        ]:
            if not db.scalar(select(User).where(User.username == username)):
                user = User(
                    username=username,
                    name=name,
                    role=role,
                    password_hash=hash_password(passwords.get(username, password)),
                )
                db.add(user)
                db.flush()
        for user in db.scalars(select(User)):
            _profile(db, user)
        if include_demo_accounts:
            # Project fixtures are optional, public metadata; portable core omits them.
            fixture_path = POLICY_PATH.with_name("identity-demo.json")
            fixture = (
                json.loads(fixture_path.read_text(encoding="utf-8"))
                if fixture_path.is_file()
                else {}
            )
            for item in fixture.get("organizations", []):
                if not db.get(Organization, item["id"]):
                    db.add(Organization(**item))
            db.flush()
            for item in fixture.get("teams", []):
                if not db.get(Team, item["id"]):
                    db.add(Team(**item))
            db.flush()
            for item in fixture.get("accounts", []):
                username, name, role, organization_id, team_ids = (
                    item[key] for key in ("username", "name", "role", "organization_id", "team_ids")
                )
                if db.scalar(select(User).where(User.username == username)):
                    continue
                password = _password(passwords.get(username) or secrets.token_urlsafe(24))
                user = User(
                    username=username, name=name, role=role, password_hash=hash_password(password)
                )
                db.add(user)
                db.flush()
                user.identity_profile = IdentityProfile(
                    user_id=user.id,
                    roles=[role],
                    organization_id=organization_id,
                    authorization_revision=1,
                )
                user.team_memberships = [
                    TeamMembership(user_id=user.id, team_id=team_id) for team_id in team_ids
                ]
                credentials.append(
                    {"username": username, "name": name, "role": role, "initial_password": password}
                )
        db.add(IdentityBootstrap(key="identity-v04"))
        audit(
            db,
            "bootstrap",
            "identity.bootstrap",
            "identity-v04",
            canonical_accounts_created=len(credentials),
        )
        db.flush()
        if credentials and data_dir is not None:
            destination = Path(data_dir) / "identity-demo-accounts.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "purpose": "本地演示账户，仅首次生成；正式部署请使用正式初始化或外部 OIDC。",
                        "accounts": credentials,
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
