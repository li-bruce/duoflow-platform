"""Domain-neutral, owner-private authority used to exercise the platform seams.

The installed flow declaration is also the executable transition table. The
module owns its tables; the host owns identity, invocation receipts and the
transaction. Never commit the injected session: the object, audit and durable
invocation receipt must succeed or roll back together.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select, update

from app.contracts import CapabilityOutput
from app.db import audit, now, uid
from app.errors import require
from app.identity import has_role
from app.job_contract import validate_page_input

OBJECT_TYPE = "framework-lab.item"
FLOW_PATH = Path(__file__).resolve().parents[2] / "flows/packs/framework-lab/flow.json"
FLOW = json.loads(FLOW_PATH.read_text(encoding="utf-8"))["flows"][0]
MACHINE = FLOW["state_machine"]
metadata = MetaData()
records = Table(
    "framework_lab_items",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("owner_id", String(40), nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("state", String(30), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("feedback", Text, nullable=False),
    Column("history", Text, nullable=False),
    Column("created_at", String(50), nullable=False),
    Column("updated_at", String(50), nullable=False),
)


def identity(actor):
    require(
        isinstance(actor, dict)
        and isinstance(actor.get("id"), str)
        and bool(actor["id"])
        and actor.get("enabled") is not False
        and has_role(actor, {"member", "system_admin", "admin"}),
        "framework_lab_identity",
        "请使用有效的已登录账号。",
        403,
    )


def load(db, actor, identifier):
    identity(actor)
    # Deliberately no administrator exception: this example is private to its author.
    item = (
        db.execute(
            select(records).where(records.c.id == identifier, records.c.owner_id == actor["id"])
        )
        .mappings()
        .one_or_none()
    )
    require(item is not None, "object_scope", "工作项不存在或不属于当前用户。", 404)
    return item


def transitions(state, action=None):
    return [
        entry
        for entry in MACHINE["transitions"]
        if entry["from"] == state and (action is None or entry["action_id"] == action)
    ]


def projection(item, *, brief=False):
    """从正式记录构造授权读取结果和当前允许动作，供两种入口共同使用。"""
    data = {"title": item["title"], "owner_id": item["owner_id"]}
    if not brief:
        data.update(
            body=item["body"], feedback=item["feedback"], history=json.loads(item["history"])[-40:]
        )
    return {
        "object_ref": {"object_type": OBJECT_TYPE, "object_id": item["id"]},
        "revision": item["revision"],
        "state": item["state"],
        "data": data,
        "allowed_actions": sorted({entry["action_id"] for entry in transitions(item["state"])}),
        "jobs": [],
        "updated_at": item["updated_at"],
    }


def listing(db, actor, payload):
    identity(actor)
    validate_page_input("objects.list", payload)
    require(
        payload.get("object_type", OBJECT_TYPE) == OBJECT_TYPE,
        "object_type",
        "工作项类型不匹配。",
        422,
    )
    # Limit this debug package to a small collection; production modules should
    # page the authorized repository query rather than scan a large directory.
    items = list(
        db.execute(
            select(records)
            .where(records.c.owner_id == actor["id"])
            .order_by(records.c.updated_at.desc(), records.c.id)
        ).mappings()
    )
    signature = hashlib.sha256(
        json.dumps([[row["id"], row["revision"]] for row in items]).encode()
    ).hexdigest()
    binding = {
        "owner": actor["id"],
        "revision": actor.get("authorization_revision", 1),
        "snapshot": signature,
    }
    offset, limit = 0, payload.get("limit", 20)
    if payload.get("cursor"):
        try:
            raw = payload["cursor"]
            token = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except (ValueError, UnicodeError, TypeError):
            token = None
        require(
            isinstance(token, dict)
            and set(token) == {*binding, "offset"}
            and type(token["offset"]) is int,
            "cursor_invalid",
            "目录游标无效。",
            422,
        )
        require(
            all(token[key] == value for key, value in binding.items()),
            "revision_conflict",
            "列表或身份已变化，请刷新目录。",
            409,
        )
        offset = token["offset"]
        require(0 <= offset <= len(items), "cursor_invalid", "目录游标无效。", 422)
    page = items[offset : offset + limit]
    cursor = None
    if offset + len(page) < len(items):
        cursor = (
            base64.urlsafe_b64encode(json.dumps({**binding, "offset": offset + len(page)}).encode())
            .decode()
            .rstrip("=")
        )
    return {"objects": [projection(item, brief=True) for item in page], "next_cursor": cursor}


def result(ctx, identifier, summary):
    return CapabilityOutput(
        summary, {"projection": projection(load(ctx.session, ctx.actor, identifier))}
    )


def content(command):
    title, body = command["title"].strip(), command["body"].strip()
    require(bool(title), "title_required", "请填写工作项标题。", 422)
    return title, body


def create_item(ctx, payload):
    """建立本人工作项，初始状态来自共同状态定义，历史随事务保存。"""
    identity(ctx.actor)
    title, body = content(payload["command"])
    # Capacity belongs to this sample, not a global framework limit.
    count = len(
        ctx.session.execute(select(records.c.id).where(records.c.owner_id == ctx.actor["id"])).all()
    )
    require(count < 200, "framework_lab_capacity", "调试样例每位用户最多保留 200 个工作项。", 413)
    identifier, stamp = uid(), now()
    history = [
        {
            "action": "create",
            "from": None,
            "to": MACHINE["initial"],
            "revision": 1,
            "actor_id": ctx.actor["id"],
            "at": stamp,
        }
    ]
    ctx.session.execute(
        insert(records).values(
            id=identifier,
            owner_id=ctx.actor["id"],
            title=title,
            body=body,
            state=MACHINE["initial"],
            revision=1,
            feedback="",
            history=json.dumps(history),
            created_at=stamp,
            updated_at=stamp,
        )
    )
    audit(ctx.session, ctx.actor["id"], "framework-lab.create", identifier, revision=1)
    return result(ctx, identifier, "已建立本人工作项，可在页面与对话中继续调试。")


def command(ctx, payload, action):
    """按状态表选择迁移，再检查内容和修订，原子保存对象及办理历史。"""
    item = load(ctx.session, ctx.actor, payload["object_id"])
    require(
        type(payload["expected_revision"]) is int
        and item["revision"] == payload["expected_revision"],
        "revision_conflict",
        "工作项已更新，请重新读取后操作。",
        409,
    )
    matches = transitions(item["state"], action)
    require(len(matches) == 1, "state_conflict", "当前正式状态不允许此动作。", 409)
    transition = matches[0]
    changes = {"state": transition["to"]}
    if action == "edit":
        title, body = content(payload["command"])
        changes.update(title=title, body=body)
    elif action == "submit":
        require(bool(item["body"].strip()), "body_required", "提交前请保存一段工作项说明。", 422)
        changes["feedback"] = ""
    elif action in ("return", "complete"):
        note = payload["command"]["note"].strip()
        require(bool(note), "note_required", "请填写本次处理说明。", 422)
        changes["feedback"] = note
    stamp, revision = now(), item["revision"] + 1
    history = json.loads(item["history"])
    history.append(
        {
            "action": action,
            "transition_id": transition["id"],
            "from": item["state"],
            "to": transition["to"],
            "revision": revision,
            "actor_id": ctx.actor["id"],
            "at": stamp,
        }
    )
    changed = ctx.session.execute(
        update(records)
        .where(
            records.c.id == item["id"],
            records.c.owner_id == ctx.actor["id"],
            records.c.revision == item["revision"],
            records.c.state == item["state"],
        )
        .values(
            **changes,
            revision=revision,
            updated_at=stamp,
            history=json.dumps(history, ensure_ascii=False),
        )
    )
    require(changed.rowcount == 1, "revision_conflict", "另一请求已经修改此工作项。", 409)
    audit(
        ctx.session,
        ctx.actor["id"],
        "framework-lab." + action,
        item["id"],
        revision=revision,
        transition_id=transition["id"],
        from_state=item["state"],
        to_state=transition["to"],
    )
    return result(
        ctx,
        item["id"],
        {
            "edit": "内容已保存。",
            "submit": "已提交，可模拟完成或退回。",
            "return": "已退回，可修改后再次提交。",
            "complete": "已完成，正式状态不再允许修改。",
        }[action],
    )


def edit_item(ctx, payload):
    return command(ctx, payload, "edit")


def submit_item(ctx, payload):
    return command(ctx, payload, "submit")


def return_item(ctx, payload):
    return command(ctx, payload, "return")


def complete_item(ctx, payload):
    return command(ctx, payload, "complete")


def read_item(ctx, payload):
    return result(ctx, payload["object_id"], "已读取本人工作项的当前状态。")


def list_items(ctx, payload):
    return CapabilityOutput("已读取本人工作项目录。", listing(ctx.session, ctx.actor, payload))


class Adapter:
    def __init__(self, context):
        self.sessions = context.sessions
        with self.sessions() as db:
            metadata.create_all(db.get_bind())

    def request(self, *, actor, capability_id, operation, payload, request_id):
        identity(actor)
        require(
            capability_id == "framework-lab.read", "capability_missing", "对象适配能力不匹配。", 422
        )
        require(
            operation in {"objects.list", "objects.read"},
            "workflow_adapter_missing",
            "此中立样例不包含文件或长任务。",
            501,
        )
        with self.sessions() as db:
            if operation == "objects.list":
                return listing(db, actor, payload)
            ref = payload["object_ref"]
            require(ref["object_type"] == OBJECT_TYPE, "object_type", "工作项类型不匹配。", 422)
            return {"projection": projection(load(db, actor, ref["object_id"]))}


def create(context):
    return Adapter(context)
