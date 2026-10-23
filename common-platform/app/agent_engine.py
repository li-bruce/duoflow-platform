"""Local, persistent LangGraph guidance for registered conversation skills.

The graph prepares forms; it never calls a capability, a model or another service.
Only identifiers and progress are checkpointed. Business inputs/results remain in
the platform database and are re-authorized before use. SQLite targets one local
platform process, not distributed workflow execution.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from sqlalchemy import Integer, literal_column

from .db import Invocation, Message, Run, now, uid
from .errors import PlatformError, require
from .identity import load_actor, refresh_actor
from .workspace_contract import normalize_skills


class GuidanceState(TypedDict, total=False):
    owner_id: str
    conversation_id: str
    operation: str
    selection: str
    active: dict | None
    route: str
    notice: str
    choices: list[str]


NOTICES = {
    "selected": "已进入所选流程。请核对并提交当前步骤表单；流程引擎尚未执行业务。",
    "input": "当前步骤尚无成功的执行记录。填写并提交表单后，再选择继续。",
    "advanced": "已根据本会话保存的成功结果推进步骤。下方表单仍需你核对并提交。",
    "waiting": "当前步骤仍在等待执行结果，未进入下一步，也没有重复提交。",
    "failed": "当前步骤的执行未成功。请查看任务结果并处理失败或取消状态，流程没有跳过该步骤。",
    "completed": "此流程声明的步骤均已有成功记录。没有自动执行额外操作；业务最终状态以相应结果为准。",
    "exited": "已退出当前流程。已有业务记录保留，没有取消或重复执行任何业务。",
    "none": "当前没有正在办理的流程。输入 /skills 查看可用流程，或使用 /skill 模块标识/流程标识。",
    "catalog": "以下是当前身份可使用的已注册流程。选择明确的流程指令即可进入。",
    "ambiguous": "这条入口提示对应多个流程，请使用下方明确的 /skill 指令选择。",
    "unknown": "没有找到当前身份可使用的这个流程。请用 /skills 查看有效入口。",
    "invalid": "流程指令或注册声明无效，请核对 /skill 模块标识/流程标识，或联系管理员。",
    "unavailable": "当前流程已停用、发生变更或不再获授权。请重新选择可用流程，原业务不会自动重放。",
    "changed": "流程或能力版本已变化。请重新选择流程核对新版本，旧步骤不会自动切换到新目标。",
    "offline": "当前步骤对应的服务暂不可用。已保留流程位置，恢复可用后可以继续。",
    "blocked": "当前步骤需要相应权限，已保留完整流程并停止推进。请由有权角色按业务流程办理，不能跳过此步骤。",
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


EVENT_ORDER = "sqlite-insertion-v1"


def _sqlite_order(db, model):
    """Current local store has an actual insertion order; UUID/time do not.

    Store record IDs in checkpoints, resolving their rowid at each read. That
    avoids depending on numeric rowids remaining identical after maintenance.
    A production database adapter must provide a durable sequence instead.
    """
    require(
        db.bind.dialect.name == "sqlite",
        "skill_order_unsupported",
        "当前流程顺序仅支持本机 SQLite；其他数据库需要持久事件序列适配。",
        503,
    )
    return literal_column(model.__tablename__ + ".rowid", type_=Integer)


class AgentEngine:
    def __init__(self, runtime, data_dir):
        self.runtime = runtime
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "agent-checkpoints.sqlite3"
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self.checkpointer = SqliteSaver(self._connection)
        self.checkpointer.setup()
        builder = StateGraph(GuidanceState)
        builder.add_node("resolve", self._resolve)
        builder.add_node("authorize", self._authorize)
        builder.add_node("prepare_form", self._prepare_form)
        builder.add_node("reply", self._reply)
        builder.add_edge(START, "resolve")
        builder.add_edge("resolve", "authorize")
        builder.add_conditional_edges(
            "authorize", lambda state: state["route"], {"form": "prepare_form", "reply": "reply"}
        )
        builder.add_edge("prepare_form", END)
        builder.add_edge("reply", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)

    def close(self):
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    @staticmethod
    def _config(owner_id, conversation_id):
        # A child conversation always gets a separate checkpoint namespace.
        return {
            "configurable": {"thread_id": "skill-" + _digest([owner_id, conversation_id])},
            "callbacks": [],
            "recursion_limit": 8,
        }

    def _actor(self, owner_id, conversation_id):
        with self.runtime.sessions() as db:
            actor = load_actor(db, owner_id)
            self.runtime.conversation_check(db, conversation_id, actor)
            return actor

    def _verify(self, actor, conversation_id):
        with self.runtime.sessions() as db:
            current = refresh_actor(db, actor, require_unchanged=True)
            self.runtime.conversation_check(db, conversation_id, current)
            return current

    def _catalog(self, actor):
        catalog, invalid = {}, set()
        for module in self.runtime.public_registry(actor).get("modules", []):
            conversation = module.get("surfaces", {}).get("conversation", {})
            skills = conversation.get("skills", [])
            caps = [
                s
                for s in self.runtime.registry.capabilities.values()
                if s.get("module_id") == module["id"]
            ]
            if not skills or not caps:
                continue
            try:
                normalize_skills(skills, caps)
            except (PlatformError, KeyError, TypeError, ValueError):
                for skill in skills if isinstance(skills, list) else []:
                    if isinstance(skill, dict) and isinstance(skill.get("id"), str):
                        invalid.add(module["id"] + "/" + skill["id"])
                continue
            for skill in skills:
                cap_ids = {step["capability_id"] for step in skill["steps"]}
                if skill["entry_capability"] not in conversation.get("capability_ids", []):
                    continue
                try:
                    self.runtime.registry.get(skill["entry_capability"], actor)
                except PlatformError:
                    continue
                specs = [self.runtime.registry.capabilities[cap_id] for cap_id in sorted(cap_ids)]
                key = module["id"] + "/" + skill["id"]
                # Publication/connection revisions invalidate old guidance even
                # when an author accidentally retains the public semantic version.
                revision = {
                    "skill": skill,
                    "module": module["id"],
                    "version": module.get("version"),
                    "revision": module.get("revision", 1),
                    "capabilities": [(s["id"], self.runtime.execution_revision(s)) for s in specs],
                }
                catalog[key] = {
                    "module_id": module["id"],
                    "skill": deepcopy(skill),
                    "digest": _digest(revision),
                }
        return catalog, invalid

    @staticmethod
    def _command(content, catalog, invalid):
        text = content.strip().rstrip("。！!").strip()
        if text == "/skills":
            return "catalog", "", [], "catalog"
        if text == "/exit":
            return "exit", "", [], "exited"
        if text in {"/continue", "继续", "下一步"}:
            return "continue", "", [], ""
        if text == "/skill" or text.startswith("/skill "):
            target = text[6:].strip()
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,120}/[a-z][a-z0-9_.-]{0,120}", target):
                return "error", "", [], "invalid"
            if target in catalog:
                return "select", target, [], "selected"
            return "error", "", [], "invalid" if target in invalid else "unknown"
        matches = []
        for key, item in catalog.items():
            for trigger in item["skill"]["triggers"]:
                # Exact prompts and explicit entry verbs only. No fuzzy substring
                # matching of questions, negation or unrelated user data.
                if text in {
                    trigger,
                    *[
                        prefix + trigger
                        for prefix in ("开始", "启动", "进入", "使用", "请开始", "请使用")
                    ],
                }:
                    matches.append(key)
                    break
        if len(matches) == 1:
            return "select", matches[0], [], "selected"
        if matches:
            return "catalog", "", sorted(matches), "ambiguous"
        return None

    def _from_messages(self, owner_id, conversation_id):
        # In particular, never inspect parent_id's checkpoint or future runs.
        with self.runtime.sessions() as db:
            self.runtime.conversation_check(
                db, conversation_id, self._actor(owner_id, conversation_id)
            )
            messages = (
                db.query(Message)
                .filter_by(conversation_id=conversation_id, role="assistant")
                .order_by(_sqlite_order(db, Message).desc())
            )
            for message in messages:
                for view in reversed(message.views or []):
                    data = view.get("data", {}) if isinstance(view, dict) else {}
                    if (
                        not isinstance(view, dict)
                        or view.get("type") != "agent-flow"
                        or data.get("schema_version") != 1
                    ):
                        continue
                    if data.get("status") == "exited":
                        return None
                    if data.get("status") == "catalog":
                        continue
                    fields = (
                        "module_id",
                        "skill_id",
                        "skill_digest",
                        "flow_id",
                        "started_at",
                        "current_step",
                        "completed_invocations",
                        "completed_step_ids",
                        "status",
                    )
                    if all(key in data for key in fields):
                        return {
                            **{key: deepcopy(data[key]) for key in fields},
                            "event_order": data.get("event_order"),
                            "start_after_invocation_id": data.get("start_after_invocation_id"),
                            "last_completed_position": deepcopy(
                                data.get("last_completed_position", [])
                            ),
                        }
        return None

    def _saved(self, owner_id, conversation_id):
        values = self.graph.get_state(self._config(owner_id, conversation_id)).values
        if values:
            return deepcopy(values.get("active"))
        return self._from_messages(owner_id, conversation_id)

    async def prepare(self, actor, conversation_id, content):
        require(
            isinstance(content, str) and len(content) <= 24000,
            "message_length",
            "消息长度无效。",
            422,
        )
        return await asyncio.to_thread(self._prepare, deepcopy(actor), conversation_id, content)

    def _prepare(self, actor, conversation_id, content):
        # Disable LangSmith even if its tracing environment flag is enabled by
        # the host. No callbacks or external client are supplied to this graph.
        with self._lock, tracing_context(enabled=False, parent=False):
            require(not self._closed, "agent_closed", "流程引擎已停止。", 503)
            actor = self._verify(actor, conversation_id)
            catalog, invalid = self._catalog(actor)
            command = self._command(content, catalog, invalid)
            if command is None:
                return None  # Ordinary conversation remains with the model gateway.
            operation, selection, choices, notice = command
            state = {
                "owner_id": actor["id"],
                "conversation_id": conversation_id,
                "operation": operation,
                "selection": selection,
                "choices": choices,
                "notice": notice,
                "route": "reply",
                "active": self._saved(actor["id"], conversation_id),
            }
            output = self.graph.invoke(state, self._config(actor["id"], conversation_id))
            actor = self._verify(actor, conversation_id)
            result = self._render(actor, conversation_id, output)
            return json.loads(self.runtime.redact(json.dumps(result, ensure_ascii=False)))

    def _resolve(self, state):
        if state["operation"] == "exit":
            return {"active": None, "notice": "exited"}
        if state["operation"] == "select":
            actor = self._actor(state["owner_id"], state["conversation_id"])
            item = self._catalog(actor)[0].get(state["selection"])
            if not item:
                return {"active": None, "notice": "unavailable"}
            with self.runtime.sessions() as db:
                previous = (
                    db.query(Invocation.id)
                    .filter_by(actor_id=actor["id"], conversation_id=state["conversation_id"])
                    .order_by(_sqlite_order(db, Invocation).desc())
                    .first()
                )
            return {
                "active": {
                    "module_id": item["module_id"],
                    "skill_id": item["skill"]["id"],
                    "skill_digest": item["digest"],
                    "flow_id": uid(),
                    "started_at": now(),
                    "current_step": 0,
                    "completed_invocations": [],
                    "completed_step_ids": [],
                    "event_order": EVENT_ORDER,
                    "start_after_invocation_id": previous[0] if previous else None,
                    "last_completed_position": [],
                    "status": "awaiting_input",
                },
                "notice": "selected",
            }
        if state["operation"] == "continue" and not state.get("active"):
            return {"notice": "none"}
        return {}

    def _authorize(self, state):
        actor = self._actor(state["owner_id"], state["conversation_id"])
        if state["operation"] in {"catalog", "exit", "error"} or not state.get("active"):
            return {"route": "reply"}
        active = state["active"]
        if active.get("event_order") != EVENT_ORDER:
            return {"route": "reply", "notice": "changed"}
        item = self._catalog(actor)[0].get(active["module_id"] + "/" + active["skill_id"])
        if not item:
            return {"route": "reply", "notice": "unavailable"}
        if item["digest"] != active["skill_digest"]:
            return {"route": "reply", "notice": "changed"}
        require(
            type(active["current_step"]) is int
            and 0 <= active["current_step"] <= len(item["skill"]["steps"]),
            "skill_state",
            "流程位置无效，请重新选择流程。",
            409,
        )
        if active["current_step"] < len(item["skill"]["steps"]):
            try:
                spec = self.runtime.registry.get(
                    item["skill"]["steps"][active["current_step"]]["capability_id"], actor
                )
            except PlatformError:
                return {"route": "reply", "notice": "blocked"}
            try:
                self.runtime.check_available(spec)
            except PlatformError:
                return {"route": "reply", "notice": "offline"}
        return {"route": "form"}

    def _events(self, actor, conversation_id, active):
        events = []
        with self.runtime.sessions() as db:
            self.runtime.conversation_check(db, conversation_id, actor)
            sequence = _sqlite_order(db, Invocation)
            query = db.query(Invocation).filter_by(
                actor_id=actor["id"], conversation_id=conversation_id
            )
            # Both the flow boundary and progress cursor reference immutable IDs,
            # not wall-clock values or numeric rowids. Never silently reset a
            # missing cursor, which could replay an earlier successful operation.
            anchors = [active.get("start_after_invocation_id")]
            if active["completed_invocations"]:
                anchors.append(active["completed_invocations"][-1])
            for anchor_id in filter(None, anchors):
                anchor = (
                    db.query(sequence)
                    .select_from(Invocation)
                    .filter_by(id=anchor_id, actor_id=actor["id"])
                    .first()
                )
                require(
                    anchor is not None,
                    "skill_cursor_missing",
                    "流程顺序引用已不存在，请重新选择流程；旧操作不会重放。",
                    409,
                )
                query = query.filter(sequence > anchor[0])
            for invocation in query.order_by(sequence):
                if invocation.id in active["completed_invocations"]:
                    continue
                # Kept as a human-readable compatibility field only. Ordering
                # is always resolved from the invocation ID above.
                position = [invocation.created_at, invocation.id]
                try:
                    spec = self.runtime.registry.get(invocation.capability_id, actor)
                    self.runtime.check_invocation_authorization(invocation, actor)
                    if invocation.capability_version != self.runtime.execution_revision(spec):
                        continue
                except PlatformError:
                    continue
                run = db.query(Run).filter_by(invocation_id=invocation.id).first()
                complete = (
                    invocation.status == "completed"
                    and bool(invocation.result)
                    and (run is None or run.status == "succeeded")
                )
                result = None
                if complete:
                    try:
                        # Copies prevent a projection helper from mutating ORM JSON.
                        result = self.runtime.refresh_result(db, deepcopy(invocation.result), actor)
                    except PlatformError:
                        continue
                events.append(
                    {
                        "id": invocation.id,
                        "capability_id": invocation.capability_id,
                        "position": position,
                        "complete": complete,
                        "result": result,
                        "run_id": run.id if run else None,
                        "status": run.status if run else invocation.status,
                    }
                )
        return events

    def _prepare_form(self, state):
        actor = self._actor(state["owner_id"], state["conversation_id"])
        active = deepcopy(state["active"])
        item = self._catalog(actor)[0].get(active["module_id"] + "/" + active["skill_id"])
        if not item or item["digest"] != active["skill_digest"]:
            return {"notice": "changed"}
        steps = item["skill"]["steps"]
        advanced = False
        if state["operation"] == "continue":
            for event in self._events(actor, state["conversation_id"], active):
                index = active["current_step"]
                if index >= len(steps):
                    break
                if event["capability_id"] == steps[index]["capability_id"] and event["complete"]:
                    active["completed_invocations"].append(event["id"])
                    active["completed_step_ids"].append(steps[index]["id"])
                    active["last_completed_position"] = event["position"]
                    active["current_step"] += 1
                    advanced = True
        if active["current_step"] >= len(steps):
            active["status"] = "completed"
            return {"active": active, "notice": "completed"}
        capability = steps[active["current_step"]]["capability_id"]
        pending = [
            e
            for e in self._events(actor, state["conversation_id"], active)
            if e["capability_id"] == capability and not e["complete"]
        ]
        active["status"] = (
            ("failed" if pending[-1]["status"] in {"failed", "cancelled"} else "awaiting_result")
            if pending
            else "awaiting_input"
        )
        notice = (
            ("failed" if active["status"] == "failed" else "waiting")
            if pending
            else (
                "advanced"
                if advanced
                else "selected" if state["operation"] == "select" else "input"
            )
        )
        return {"active": active, "notice": notice}

    @staticmethod
    def _reply(state):
        return {"route": "reply"}

    def _catalog_view(self, actor, conversation_id, choices=()):
        catalog = self._catalog(actor)[0]
        return {
            "engine": "langgraph",
            "schema_version": 1,
            "conversation_id": conversation_id,
            "status": "catalog",
            "title": "已注册流程",
            "steps": [],
            "skills": [
                {
                    "module_id": item["module_id"],
                    "skill_id": item["skill"]["id"],
                    "title": item["skill"]["title"],
                    "command": "/skill " + key,
                }
                for key, item in catalog.items()
                if not choices or key in choices
            ],
        }

    def refresh_view(self, actor, conversation_id, data):
        actor = self._verify(actor, conversation_id)
        require(isinstance(data, dict), "skill_state", "流程引用无效。", 422)
        if data.get("status") == "catalog":
            return self._catalog_view(actor, conversation_id)
        if data.get("status") == "exited":
            return {
                "engine": "langgraph",
                "schema_version": 1,
                "conversation_id": conversation_id,
                "status": "exited",
                "title": "已退出流程",
                "steps": [],
                "current_step": 0,
            }
        item = self._catalog(actor)[0].get(
            str(data.get("module_id")) + "/" + str(data.get("skill_id"))
        )
        require(item is not None, "skill_unavailable", NOTICES["unavailable"], 403)
        require(
            data.get("skill_digest") == item["digest"], "skill_changed", NOTICES["changed"], 409
        )
        require(
            data.get("event_order") == EVENT_ORDER,
            "skill_order_changed",
            "流程顺序格式已升级，请重新选择流程；已有业务记录保留。",
            409,
        )
        index = data.get("current_step")
        require(
            type(index) is int and 0 <= index <= len(item["skill"]["steps"]),
            "skill_state",
            "流程位置无效。",
            409,
        )
        fields = (
            "module_id",
            "skill_id",
            "skill_digest",
            "flow_id",
            "started_at",
            "current_step",
            "completed_invocations",
            "completed_step_ids",
            "status",
        )
        result = {key: deepcopy(data.get(key)) for key in fields}
        result["event_order"] = data["event_order"]
        result["start_after_invocation_id"] = data.get("start_after_invocation_id")
        result["last_completed_position"] = deepcopy(data.get("last_completed_position", []))
        result.update(
            engine="langgraph",
            schema_version=1,
            conversation_id=conversation_id,
            title=item["skill"]["title"],
            steps=[
                {
                    **step,
                    "status": "completed" if i < index else "current" if i == index else "pending",
                }
                for i, step in enumerate(item["skill"]["steps"])
            ],
        )
        if index < len(item["skill"]["steps"]):
            try:
                spec = self.runtime.registry.get(
                    item["skill"]["steps"][index]["capability_id"], actor
                )
            except PlatformError:
                result.update(status="blocked", blocked_reason="permission")
                return result
            try:
                self.runtime.check_available(spec)
            except PlatformError:
                result.update(status="blocked", blocked_reason="availability")
        return result

    def _next_inputs(self, actor, conversation_id, active, capability_id):
        # Only the last *completed* step can propose inputs for the next step.
        # Proposals stay ephemeral; raw business values never enter checkpoints.
        if not active["completed_invocations"]:
            return []
        with self.runtime.sessions() as db:
            invocation = db.get(Invocation, active["completed_invocations"][-1])
            if (
                not invocation
                or invocation.actor_id != actor["id"]
                or invocation.conversation_id != conversation_id
            ):
                return []  # A branch never follows the parent's live result.
            try:
                self.runtime.check_invocation_authorization(invocation, actor)
                result = self.runtime.refresh_result(db, deepcopy(invocation.result), actor)
            except PlatformError:
                return []
        actions = []

        def visit(value, depth=0):
            if not isinstance(value, dict) or depth > 4:
                return
            for block in value.get("blocks", []):
                if isinstance(block, dict) and block.get("type") == "actions":
                    actions.extend(a for a in block.get("items", []) if isinstance(a, dict))
            for view in value.get("views", []):
                if isinstance(view, dict):
                    visit(view.get("data"), depth + 1)
            if isinstance(value.get("result"), dict):
                visit(value["result"], depth + 1)
            if isinstance(value.get("data"), dict):
                visit(value["data"], depth + 1)

        visit(result)
        properties = self.runtime.registry.get(capability_id, actor)["input_schema"].get(
            "properties", {}
        )
        forms, seen = [], set()
        for action in actions:
            if action.get("capability_id") != capability_id or not isinstance(
                action.get("input"), dict
            ):
                continue
            payload = {key: value for key, value in action["input"].items() if key in properties}
            encoded = json.dumps(payload, ensure_ascii=False)
            if (
                len(encoded.encode("utf-8")) > 100000
                or self.runtime.redact(encoded) != encoded
                or encoded in seen
            ):
                continue
            seen.add(encoded)
            forms.append(payload)
        return forms[:4]

    def _render(self, actor, conversation_id, state):
        notice = state.get("notice") or "none"
        response = {"content": NOTICES.get(notice, NOTICES["unavailable"]), "views": []}
        if state["operation"] == "catalog":
            response["views"] = [
                {
                    "type": "agent-flow",
                    "data": self._catalog_view(actor, conversation_id, state.get("choices")),
                }
            ]
            return response
        if state["operation"] == "exit":
            response["views"] = [
                {
                    "type": "agent-flow",
                    "data": self.refresh_view(actor, conversation_id, {"status": "exited"}),
                }
            ]
            return response
        if notice in {"unknown", "invalid", "none", "unavailable", "changed"} or not state.get(
            "active"
        ):
            return response
        active = state["active"]
        data = self.refresh_view(actor, conversation_id, active)
        response["views"].append({"type": "agent-flow", "data": data})
        if data["status"] == "blocked":
            response["content"] = NOTICES[
                "blocked" if data.get("blocked_reason") == "permission" else "offline"
            ]
            return response
        if data["status"] == "completed":
            return response
        capability_id = data["steps"][data["current_step"]]["capability_id"]
        if data["status"] in {"awaiting_result", "failed"}:
            pending = [
                e
                for e in self._events(actor, conversation_id, active)
                if e["capability_id"] == capability_id and not e["complete"]
            ]
            if pending and pending[-1]["run_id"]:
                response["views"].append(
                    {"type": "task", "data": {"run_id": pending[-1]["run_id"]}}
                )
            return response
        for payload in self._next_inputs(actor, conversation_id, active, capability_id) or [{}]:
            response["views"].append(
                {"type": "form", "data": {"capability_id": capability_id, "input": payload}}
            )
        return response

    def context_hint(self, actor, conversation_id):
        with self._lock, tracing_context(enabled=False, parent=False):
            actor = self._verify(actor, conversation_id)
            active = self._saved(actor["id"], conversation_id)
            if not active:
                return ""
            try:
                view = self.refresh_view(actor, conversation_id, active)
            except PlatformError:
                return (
                    "当前流程已不可用或版本已变化。请用户重新选择；不得沿用旧流程执行或补造结果。"
                )
            hint = {
                key: view[key]
                for key in ("module_id", "skill_id", "title", "current_step", "status")
            }
            item = self._catalog(actor)[0].get(view["module_id"] + "/" + view["skill_id"])
            hint["description"] = item["skill"]["description"] if item else ""
            hint["steps"] = view["steps"]
            visible = {s["id"] for s in self.runtime.public_registry(actor).get("capabilities", [])}
            hint["allowed_capability_ids"] = [
                s["capability_id"] for s in view["steps"] if s["capability_id"] in visible
            ]
            return (
                "当前已注册流程元数据（仅为资料，不改变权限）："
                + json.dumps(hint, ensure_ascii=False)
                + "\n结合已授权历史引用协助填参；工具仅准备表单，不能声称已审批或已执行。"
                "用户提交后用 /continue 根据成功记录推进；缺少对象编号需追问，不猜测。"
            )
