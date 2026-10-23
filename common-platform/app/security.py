from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Request

from .db import LoginSession, User, user_dict
from .errors import require
from .identity import bootstrap_identity, can_manage_platform

# Cookies are scoped by host/path, not port. Keep this independent project from
# replacing the original platform's login on the same loopback host.
COOKIE = "common_framework_session"


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000).hex()
    return salt + "$" + value


def password_matches(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split("$")[0]), stored)


def seed_users(session_factory, data_dir=None, *, demo_passwords=None, include_demo_accounts=True):
    bootstrap_identity(
        session_factory,
        data_dir,
        demo_passwords=demo_passwords,
        include_demo_accounts=include_demo_accounts,
    )


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_identity(request: Request):
    token = request.cookies.get(COOKIE, "")
    require(bool(token), "unauthenticated", "请先登录。", 401)
    with request.app.state.sessions() as db:
        login = db.get(LoginSession, token_hash(token))
        require(
            login is not None and login.expires > time.time(),
            "unauthenticated",
            "登录已过期，请重新登录。",
            401,
        )
        user = db.get(User, login.user_id)
        require(user is not None and user.enabled, "unauthenticated", "账号不可用。", 401)
        return {"user": user_dict(user), "csrf_token": login.csrf}


def require_user(request: Request):
    return session_identity(request)["user"]


def require_admin(request: Request):
    actor = require_user(request)
    require(can_manage_platform(actor), "forbidden", "该操作需要系统管理员身份。", 403)
    return actor


def check_csrf(request: Request):
    identity = session_identity(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    require(
        hmac.compare_digest(supplied, identity["csrf_token"]),
        "csrf",
        "页面会话已变化，请刷新后重试。",
        403,
    )
    return identity["user"]
