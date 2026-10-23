"""Trusted component packs, owner-scoped instances and atomic capability actions.

UI code is installed at build time. No remote JavaScript, model-supplied handlers,
credentials or arbitrary target URLs are accepted by this interface.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from .contracts import CapabilityOutput
from .db import Conversation, InteractionInstance, Invocation, Message, Run, audit, now, uid
from .errors import PlatformError, require
from .identity import refresh_actor
from .registry import Registry, inside


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class Interactions:
    def __init__(self, runtime):
        self.runtime = runtime
        self.definitions = {}
        root = runtime.root / "components"
        installed = root / "installed.json"
        if not installed.exists():
            return
        schema = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "contracts/interactions/v1/definition.schema.json"
            ).read_text(encoding="utf-8")
        )
        for pack in json.loads(installed.read_text(encoding="utf-8-sig"))["packs"]:
            require(
                isinstance(pack, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,60}", pack),
                "component_pack",
                "组件包名称无效。",
                422,
            )
            definition = json.loads(
                inside(root, f"packs/{pack}/interaction.json").read_text(encoding="utf-8-sig")
            )
            runtime.registry.validate(schema, definition)
            for name in ("input_schema", "state_schema"):
                from jsonschema import Draft202012Validator

                Draft202012Validator.check_schema(definition[name])
                Registry._check_refs(definition[name])
                require(
                    definition[name].get("type") == "object",
                    "component_schema",
                    "组件输入和状态必须为对象。",
                    422,
                )
            runtime.registry.validate(definition["state_schema"], definition["initial_state"])
            require(
                definition["id"] not in self.definitions,
                "component_duplicate",
                "组件标识重复。",
                422,
            )
            ids = [a["id"] for a in definition["actions"]]
            require(len(ids) == len(set(ids)), "component_action", "组件动作标识重复。", 422)
            self.definitions[definition["id"]] = {
                **definition,
                "pack": pack,
                "definition_hash": digest(definition),
            }

    def definition(self, component_id, actor):
        definition = self.definitions.get(component_id)
        require(definition is not None, "component_missing", "组件未安装或已卸载。", 404)
        for action in definition["actions"]:
            spec = self.runtime.registry.get(action["capability_id"], actor)
            require(
                spec["version"] == action["capability_version"],
                "component_version",
                "组件绑定的能力版本已变化，需要重新验证接入。",
                409,
            )
        return definition

    def catalog(self, actor):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            items = []
            for component_id in self.definitions:
                try:
                    item = deepcopy(self.definition(component_id, actor))
                    item["available"] = all(
                        self.runtime.availability(
                            self.runtime.registry.get(a["capability_id"], actor)
                        )["available"]
                        for a in item["actions"]
                    )
                    items.append(item)
                except PlatformError:
                    continue
            return {"protocol_version": "1.0", "items": items}

    def load(self, db, actor, instance_id):
        actor = refresh_actor(db, actor, require_unchanged=True)
        item = db.get(InteractionInstance, instance_id)
        require(
            item is not None and item.owner_id == actor["id"],
            "interaction_scope",
            "无权访问此交互实例。",
            403,
        )
        require(
            item.authorization_revision == actor.get("authorization_revision", 1),
            "authorization_changed",
            "此交互对应的授权已变化，请重新创建。",
            403,
        )
        if item.access_stamp:
            require(
                item.access_stamp == self.runtime.access.authorization_stamp(actor),
                "authorization_changed",
                "业务授权已变化，请重新创建交互。",
                403,
            )
        definition = self.definition(item.component_id, actor)
        require(
            item.component_version == definition["version"]
            and item.definition_hash == definition["definition_hash"],
            "component_version",
            "组件版本或定义已变化，旧状态暂不可编辑；请新建实例。",
            409,
        )
        return item, definition

    def public(self, item, definition, actor):
        value = {
            key: deepcopy(getattr(item, key))
            for key in (
                "id",
                "component_id",
                "component_version",
                "conversation_id",
                "forked_from",
                "input",
                "state",
                "revision",
                "status",
                "result",
                "run_id",
                "created_at",
                "updated_at",
            )
        }
        if item.run_id:
            db = object_session(item)
            run = self.runtime.current_run_view(db, db.get(Run, item.run_id), actor)
            action = self.action_definition(definition, item.active_action)
            value["status"] = (
                ("completed" if action["completes"] else "editing")
                if run["status"] == "succeeded"
                else run["status"]
            )
            value["result"] = run.get("result")
            value["error"] = run.get("error")
        elif item.result:
            value["result"] = self.runtime.refresh_result(
                object_session(item), item.result, actor, invocation_id=item.result.get("id")
            )
        value["actions"] = [
            {
                **a,
                "available": self.runtime.availability(
                    self.runtime.registry.get(a["capability_id"], actor)
                )["available"],
            }
            for a in definition["actions"]
        ]
        value["title"] = definition["title"]
        return value

    def get(self, actor, instance_id):
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            return self.public(item, definition, actor)

    def list(self, actor):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            items = []
            for item in (
                db.query(InteractionInstance)
                .filter_by(owner_id=actor["id"])
                .order_by(InteractionInstance.updated_at.desc())
                .limit(100)
            ):
                try:
                    item, definition = self.load(db, actor, item.id)
                    value = self.public(item, definition, actor)
                    items.append(
                        {
                            k: value[k]
                            for k in (
                                "id",
                                "component_id",
                                "title",
                                "status",
                                "conversation_id",
                                "updated_at",
                            )
                        }
                    )
                except PlatformError:
                    continue
            return {"items": items}

    def validate_safe(self, schema, value):
        self.runtime.registry.validate(schema, value)
        encoded = json.dumps(value, ensure_ascii=False)
        require(
            len(encoded.encode("utf-8")) <= 64000,
            "interaction_size",
            "组件输入或草稿超过 64 KB。",
            413,
        )
        require(
            self.runtime.redact(encoded) == encoded,
            "secret_in_input",
            "组件输入不能包含已配置的服务密钥。",
            422,
        )

        def valid(data):
            if isinstance(data, dict):
                return "content_base64" not in data and all(valid(v) for v in data.values())
            if isinstance(data, list):
                return all(valid(v) for v in data)
            return True

        require(
            valid(value),
            "interaction_file",
            "组件状态请保存已上传对象的引用，不能保存文件字节。",
            422,
        )

    def create_in(self, db, actor, component_id, initial, key, conversation_id=None):
        actor = refresh_actor(db, actor, require_unchanged=True)
        definition = self.definition(component_id, actor)
        self.validate_safe(definition["input_schema"], initial)
        if conversation_id:
            self.runtime.conversation_check(db, conversation_id, actor)
        creation_hash = digest(
            {"component": component_id, "input": initial, "conversation": conversation_id}
        )
        existing = (
            db.query(InteractionInstance).filter_by(owner_id=actor["id"], creation_key=key).first()
        )
        if existing:
            require(
                existing.creation_hash == creation_hash,
                "idempotency_conflict",
                "实例创建编号已经用于其他内容。",
                409,
            )
            self.load(db, actor, existing.id)
            return existing, definition
        item = InteractionInstance(
            owner_id=actor["id"],
            authorization_revision=actor.get("authorization_revision", 1),
            component_id=component_id,
            access_stamp=(
                self.runtime.access.authorization_stamp(actor)
                if any(
                    self.runtime.access.module_registered(
                        self.runtime.registry.get(a["capability_id"], actor)["module_id"]
                    )
                    for a in definition["actions"]
                )
                else None
            ),
            component_version=definition["version"],
            definition_hash=definition["definition_hash"],
            creation_key=key,
            creation_hash=creation_hash,
            conversation_id=conversation_id,
            input=deepcopy(initial),
            state=deepcopy(definition["initial_state"]),
        )
        db.add(item)
        db.flush()
        audit(db, actor["id"], "interaction.create", item.id, component_id=component_id)
        return item, definition

    def view(self, item):
        return {
            "type": "interaction",
            "data": {
                "instance_id": item.id,
                "component_id": item.component_id,
                "component_version": item.component_version,
            },
        }

    def append(self, db, item):
        if (
            item.conversation_id
            and not db.query(Message)
            .filter_by(conversation_id=item.conversation_id, request_key="interaction-" + item.id)
            .first()
        ):
            conv = db.get(Conversation, item.conversation_id)
            conv.updated_at = now()
            db.add(
                Message(
                    conversation_id=item.conversation_id,
                    role="assistant",
                    content="可以直接操作下方组件；编辑会自动保存。",
                    views=[self.view(item)],
                    request_key="interaction-" + item.id,
                )
            )

    def create(self, actor, component_id, initial, key, conversation_id=None):
        try:
            with self.runtime.sessions.begin() as db:
                item, definition = self.create_in(
                    db, actor, component_id, initial, key, conversation_id
                )
                self.append(db, item)
                return self.public(item, definition, actor)
        except IntegrityError:
            with self.runtime.sessions.begin() as db:
                existing = (
                    db.query(InteractionInstance)
                    .filter_by(owner_id=actor["id"], creation_key=key)
                    .first()
                )
                require(
                    existing is not None,
                    "concurrent_request",
                    "实例正在另一个请求中创建，请稍后重试。",
                    409,
                )
                item, definition = self.create_in(
                    db, actor, component_id, initial, key, conversation_id
                )
                return self.public(item, definition, actor)

    def patch(self, actor, instance_id, state, revision):
        with self.runtime.sessions.begin() as db:
            item, definition = self.load(db, actor, instance_id)
            self.ensure_editable(db, item)
            self.validate_safe(definition["state_schema"], state)
            result = db.execute(
                update(InteractionInstance)
                .where(InteractionInstance.id == item.id, InteractionInstance.revision == revision)
                .values(
                    state=deepcopy(state),
                    revision=revision + 1,
                    status="editing",
                    result=None,
                    run_id=None,
                    active_action=None,
                    updated_at=now(),
                )
            )
            require(
                result.rowcount == 1,
                "revision_conflict",
                "另一窗口已修改此交互，请重新读取后继续。",
                409,
            )
            audit(db, actor["id"], "interaction.state", item.id, revision=revision + 1)
            db.flush()
            db.expire(item)
            return self.public(item, definition, actor)

    def payload(self, item, action):
        result = {}
        for field, binding in action["bindings"].items():
            source = getattr(item, binding["source"])
            require(
                binding["key"] in source,
                "interaction_input",
                "动作缺少组件声明的输入，请先填写。",
                422,
            )
            result[field] = deepcopy(source[binding["key"]])
        return result

    def action_definition(self, definition, action_id):
        action = next((a for a in definition["actions"] if a["id"] == action_id), None)
        require(action is not None, "interaction_action", "组件没有声明该动作。", 422)
        return action

    async def action(self, actor, instance_id, action_id, revision, key):
        guard = {"instance_id": instance_id, "action_id": action_id, "expected_revision": revision}
        invocation_key = "ix-" + digest({"instance": instance_id, "key": key})
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            action = self.action_definition(definition, action_id)
            existing = (
                db.query(Invocation)
                .filter_by(actor_id=actor["id"], idempotency_key=invocation_key)
                .first()
            )
            payload = existing.input if existing else self.payload(item, action)
            conversation_id = item.conversation_id
        result = await self.runtime.invoke(
            actor,
            action["capability_id"],
            payload,
            invocation_key,
            "conversation" if conversation_id else "page",
            conversation_id,
            interaction_guard=guard,
        )
        return {"instance": self.get(actor, instance_id), "result": result}

    def begin_action(self, db, actor, guard, spec, payload):
        item, definition = self.load(db, actor, guard["instance_id"])
        self.ensure_editable(db, item)
        action = self.action_definition(definition, guard["action_id"])
        require(
            action["capability_id"] == spec["id"] and self.payload(item, action) == payload,
            "interaction_changed",
            "交互输入已变化，请重新提交。",
            409,
        )
        revision = guard["expected_revision"]
        result = db.execute(
            update(InteractionInstance)
            .where(InteractionInstance.id == item.id, InteractionInstance.revision == revision)
            .values(revision=revision + 1, status="executing", updated_at=now())
        )
        require(
            result.rowcount == 1, "revision_conflict", "组件状态已变化，请重新读取后提交。", 409
        )

    def finish_action(self, db, actor, guard, result):
        item, definition = self.load(db, actor, guard["instance_id"])
        action = self.action_definition(definition, guard["action_id"])
        item.result = deepcopy(result)
        item.run_id = result.get("run_id")
        item.active_action = action["id"]
        item.status = "queued" if item.run_id else "completed" if action["completes"] else "editing"
        item.updated_at = now()
        audit(
            db,
            actor["id"],
            "interaction.action",
            item.id,
            action_id=action["id"],
            invocation_id=result["id"],
        )

    @staticmethod
    def ensure_editable(db, item):
        if item.run_id:
            run = db.get(Run, item.run_id)
            require(
                run is not None and run.status not in ("queued", "running"),
                "interaction_running",
                "动作仍在执行，请完成或停止任务后再编辑。",
                409,
            )

    def attach(self, actor, instance_id):
        with self.runtime.sessions.begin() as db:
            item, _ = self.load(db, actor, instance_id)
            if not item.conversation_id:
                conv = Conversation(
                    owner_id=actor["id"], title=self.definitions[item.component_id]["title"]
                )
                db.add(conv)
                db.flush()
                # Revision check prevents concurrent attach or edits silently winning.
                changed = db.execute(
                    update(InteractionInstance)
                    .where(
                        InteractionInstance.id == item.id,
                        InteractionInstance.revision == item.revision,
                        InteractionInstance.conversation_id.is_(None),
                    )
                    .values(conversation_id=conv.id, revision=item.revision + 1, updated_at=now())
                )
                require(
                    changed.rowcount == 1, "revision_conflict", "实例刚刚变化，请刷新后继续。", 409
                )
                db.expire(item)
            self.append(db, item)
            return {"conversation_id": item.conversation_id, "instance_id": item.id}

    def clone_view(self, db, actor, view, conversation_id, mapping):
        original_id = view["data"]["instance_id"]
        if original_id not in mapping:
            source, definition = self.load(db, actor, original_id)
            snapshot = self.public(source, definition, actor)
            clone, _ = self.create_in(
                db, actor, source.component_id, source.input, "fork-" + uid(), conversation_id
            )
            clone.state = deepcopy(source.state)
            clone.result = (
                deepcopy(snapshot["result"]) if snapshot["status"] == "completed" else None
            )
            clone.status = "completed" if snapshot["status"] == "completed" else "editing"
            clone.forked_from = source.id
            db.flush()
            mapping[original_id] = clone
        return self.view(mapping[original_id])

    def open_capability(self, ctx, payload):
        invocation = ctx.session.get(Invocation, ctx.invocation_id)
        item, _ = self.create_in(
            ctx.session,
            ctx.actor,
            payload["component_id"],
            payload.get("initial_input", {}),
            "open-" + ctx.invocation_id,
            invocation.conversation_id,
        )
        return CapabilityOutput(
            "已打开共享组件，尚未执行业务动作。", {"instance_id": item.id}, views=[self.view(item)]
        )
