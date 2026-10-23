"""Declarative UI flows over module-owned, authorized business objects.

Instances contain only private drafts, a cursor and an object reference. Reading
a flow always obtains a fresh projection from its installed module adapter.
Forking copies navigation, never invokes an action or clones a business object.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from sqlalchemy import update

from .contracts import CapabilityOutput
from .db import Conversation, Invocation, Message, Run, WorkflowInstance, audit, now, uid
from .errors import PlatformError, require
from .identity import refresh_actor
from .interactions import digest
from .job_contract import validate_page_input
from .registry import Registry, inside
from .state_machines import validate_state_machine
from .workflow_transport import LocalWorkflowContext, MAX_JSON_RESPONSE_BYTES, WorkflowTransport


class Workflows:
    def __init__(self, runtime):
        self.runtime = runtime
        adapters = {}
        for module_id, factory in runtime.local_registry.workflow_adapter_factories.items():
            context = LocalWorkflowContext(
                sessions=runtime.sessions,
                data_dir=runtime.data_dir / "workflow-modules" / digest(module_id),
                module_id=module_id,
            )
            try:
                adapter = factory(context)
            except Exception:
                raise PlatformError(
                    "workflow_adapter_init", "已安装本地流程适配器初始化失败。", 500
                ) from None
            require(
                callable(getattr(adapter, "request", None)),
                "workflow_adapter_init",
                "本地流程适配器必须实现 request。",
                500,
            )
            adapters[module_id] = adapter
        self.transport = WorkflowTransport(runtime, local_adapters=adapters)
        self.definitions = {}
        root = runtime.root / "flows"
        installed = root / "installed.json"
        if not installed.exists():
            return
        schema = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "contracts/workflows/v1/definition.schema.json"
            ).read_text(encoding="utf-8")
        )
        for pack in json.loads(installed.read_text(encoding="utf-8-sig"))["packs"]:
            require(
                isinstance(pack, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,60}", pack),
                "flow_pack",
                "流程包名称无效。",
                422,
            )
            document = json.loads(
                inside(root, f"packs/{pack}/flow.json").read_text(encoding="utf-8-sig")
            )
            runtime.registry.validate(schema, document)
            for definition in document["flows"]:
                require(
                    definition["id"] not in self.definitions,
                    "flow_duplicate",
                    "流程编号重复。",
                    422,
                )
                self.validate_definition(definition)
                self.definitions[definition["id"]] = {
                    **definition,
                    "pack": pack,
                    "definition_hash": digest(definition),
                }

    @staticmethod
    def validate_definition(definition):
        """Shared semantic validation for installation and read-only previews."""
        step_ids = [s["id"] for s in definition["steps"]]
        require(
            len(step_ids) == len(set(step_ids)) and definition["initial_step"] in step_ids,
            "flow_steps",
            "流程步骤编号无效。",
            422,
        )
        groups = definition.get("page_groups", [])
        require(
            len({group["id"] for group in groups}) == len(groups)
            and all(step in step_ids for group in groups for step in group["steps"]),
            "flow_page_groups",
            "页面分组编号重复或包含未声明步骤。",
            422,
        )
        for step in definition["steps"]:
            ids = [a["id"] for a in step["actions"]]
            require(len(ids) == len(set(ids)), "flow_actions", "步骤动作编号重复。", 422)
            for action in step["actions"]:
                Draft202012Validator.check_schema(action["command_schema"])
                Registry._check_refs(action["command_schema"])
                require(
                    action["command_schema"].get("type") == "object",
                    "flow_schema",
                    "动作输入必须为对象。",
                    422,
                )
                require(
                    not action.get("target_step") or action["target_step"] in step_ids,
                    "flow_target",
                    "动作目标步骤未声明。",
                    422,
                )
        if "state_machine" in definition:
            validate_state_machine(definition["state_machine"], definition)

    def definition(self, flow_id, actor):
        value = self.definitions.get(flow_id)
        require(value is not None, "flow_missing", "流程未安装或已卸载。", 404)
        # Opening a flow requires its read capability. Each command is separately
        # projected for the actor; a reviewer need not also be a submitter.
        spec = self.runtime.registry.get(value["provider"]["read_capability"], actor)
        access = getattr(self.runtime, "access", None)
        if access is None or not access.module_registered(spec["module_id"]):
            return value
        experiences = [
            entry
            for entry in access.experiences(actor, module_id=spec["module_id"])
            if entry["flow_id"] == flow_id
        ]
        require(bool(experiences), "flow_experience", "当前授权没有此业务的办理入口。", 403)
        # Experiences organize access to a journey, never grant a business
        # command. Object scope and state are rechecked by the module authority.
        # Keep the installed hash unchanged: personalization is not an upgrade.
        result = deepcopy(value)
        declared = {step["id"] for step in result["steps"]}
        require(
            all(set(entry.get("step_ids", declared)) <= declared for entry in experiences),
            "flow_experience_definition",
            "办理视图引用了未声明的流程步骤。",
            409,
        )
        visible = set().union(*(set(entry.get("step_ids", declared)) for entry in experiences))
        result["steps"] = [step for step in result["steps"] if step["id"] in visible]
        require(bool(result["steps"]), "flow_experience", "当前授权没有可办理的步骤。", 403)
        if result["initial_step"] not in visible:
            result["initial_step"] = result["steps"][0]["id"]
        for step in result["steps"]:
            for action in step["actions"]:
                if action.get("target_step") not in visible:
                    action.pop("target_step", None)
        if "page_groups" in result:
            result["page_groups"] = [
                {**group, "steps": [step for step in group["steps"] if step in visible]}
                for group in result["page_groups"]
                if any(step in visible for step in group["steps"])
            ]
            if not result["page_groups"]:
                result.pop("page_groups")
        return result

    def access_stamp(self, definition, actor):
        """A private instance pins access grants separately from its UI hash."""
        access = getattr(self.runtime, "access", None)
        spec = self.runtime.registry.capabilities.get(definition["provider"]["read_capability"])
        if access is not None and spec is not None and access.module_registered(spec["module_id"]):
            return access.authorization_stamp(actor)
        return None

    def public_definition(self, value, actor):
        result = deepcopy(value)
        result.pop("definition_hash", None)
        result["available"] = self.runtime.availability(
            self.runtime.registry.get(value["provider"]["read_capability"], actor)
        )["available"]
        for step in result["steps"]:
            step["actions"] = self.actions(step, actor)
        return result

    def catalog(self, actor):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            result = []
            for identifier in self.definitions:
                try:
                    result.append(self.public_definition(self.definition(identifier, actor), actor))
                except PlatformError:
                    continue
            return {"protocol_version": "1.0", "items": result}

    def load(self, db, actor, instance_id):
        actor = refresh_actor(db, actor, require_unchanged=True)
        item = db.get(WorkflowInstance, instance_id)
        require(
            item is not None and item.owner_id == actor["id"],
            "flow_scope",
            "无权访问此个人流程。",
            403,
        )
        require(
            item.authorization_revision == actor.get("authorization_revision", 1),
            "authorization_changed",
            "此流程的授权已变化，请重新打开工作台。",
            403,
        )
        definition = self.definition(item.flow_id, actor)
        current_access_stamp = self.access_stamp(definition, actor)
        require(
            getattr(item, "access_stamp", None) == current_access_stamp,
            "authorization_changed",
            "此流程的业务授权已变化，请从我的业务重新打开。",
            403,
        )
        require(
            item.flow_version == definition["version"]
            and item.definition_hash == definition["definition_hash"],
            "flow_version",
            "流程定义已经升级，请重新打开工作台。",
            409,
        )
        if current_access_stamp is not None:
            visible = {step["id"] for step in definition["steps"]}
            require(
                item.cursor in visible and set(item.drafts) <= visible,
                "flow_experience",
                "此个人流程包含当前不可访问的办理步骤，请重新打开。",
                403,
            )
        return item, definition

    @staticmethod
    def step(definition, step_id):
        value = next((s for s in definition["steps"] if s["id"] == step_id), None)
        require(value is not None, "flow_step", "未声明的流程步骤。", 422)
        return value

    def action_definition(self, definition, step_id, action_id):
        value = next(
            (a for a in self.step(definition, step_id)["actions"] if a["id"] == action_id), None
        )
        require(value is not None, "flow_action", "当前步骤没有此动作。", 422)
        return value

    @staticmethod
    def draft_query(action, spec):
        is_query = action.get("mode", "object_command") == "draft_query"
        if is_query:
            require(
                spec["kind"] == "query"
                and spec["execution"]["mode"] == "sync"
                and not action.get("creates_object"),
                "flow_query_mode",
                "草稿查询必须绑定同步查询能力，且不能创建正式业务对象。",
                409,
            )
        return is_query

    def actions(self, step, actor, projection=None, *, compact=False):
        result = []
        for action in step["actions"]:
            try:
                spec = self.runtime.registry.get(action["capability_id"], actor)
                require(
                    spec["version"] == action["capability_version"],
                    "flow_capability_version",
                    "流程能力版本已经变化。",
                    409,
                )
                is_query = self.draft_query(action, spec)
                enabled = self.runtime.availability(spec)["available"]
                if is_query or action.get("creates_object"):
                    pass  # New objects and private comparisons are not events on the selected object.
                elif projection is not None:
                    enabled = enabled and action["id"] in projection["allowed_actions"]
                elif not action.get("creates_object"):
                    enabled = False
                value = {
                    key: deepcopy(value)
                    for key, value in action.items()
                    if not compact
                    or key
                    in {
                        "id",
                        "title",
                        "capability_id",
                        "capability_version",
                        "creates_object",
                        "target_step",
                        "mode",
                    }
                }
                result.append({**value, "available": enabled})
            except PlatformError:
                continue
        return result

    def safe_draft(self, value):
        self.runtime.interactions.validate_safe({"type": "object"}, value)

    def create_in(self, db, actor, flow_id, key, conversation_id=None):
        actor = refresh_actor(db, actor, require_unchanged=True)
        definition = self.definition(flow_id, actor)
        if conversation_id:
            self.runtime.conversation_check(db, conversation_id, actor)
        creation_hash = digest({"flow": flow_id, "conversation": conversation_id})
        existing = (
            db.query(WorkflowInstance).filter_by(owner_id=actor["id"], creation_key=key).first()
        )
        if existing:
            require(
                existing.creation_hash == creation_hash,
                "idempotency_conflict",
                "创建请求已用于其他流程。",
                409,
            )
            self.load(db, actor, existing.id)
            return existing, definition
        item = WorkflowInstance(
            owner_id=actor["id"],
            authorization_revision=actor.get("authorization_revision", 1),
            flow_id=flow_id,
            flow_version=definition["version"],
            definition_hash=definition["definition_hash"],
            creation_key=key,
            creation_hash=creation_hash,
            cursor=definition["initial_step"],
            drafts={},
            object_ref=None,
            conversation_id=conversation_id,
        )
        item.access_stamp = self.access_stamp(definition, actor)
        db.add(item)
        db.flush()
        audit(db, actor["id"], "workflow.open", item.id, flow_id=flow_id)
        return item, definition

    async def create(self, actor, flow_id, key, conversation_id=None):
        async with self.runtime.lock:
            with self.runtime.sessions.begin() as db:
                item, _ = self.create_in(db, actor, flow_id, key, conversation_id)
                identifier = item.id
        return await self.get(actor, identifier)

    def view(self, item):
        return {
            "type": "workflow",
            "data": {
                "instance_id": item.id,
                "flow_id": item.flow_id,
                "flow_version": item.flow_version,
            },
        }

    def _receipt(self, db, item, actor):
        if item.run_id:
            run = self.runtime.current_run_view(db, db.get(Run, item.run_id), actor)
            return run["status"], run.get("result"), run.get("error")
        if item.last_result:
            invocation = db.get(Invocation, item.last_result["invocation_id"])
            self.runtime.check_invocation_authorization(invocation, actor)
            return "succeeded", invocation.result, None
        return "ready", None, None

    def _adopt_result(self, db, item, definition, actor):
        status, result, error = self._receipt(db, item, actor)
        invocation_id = result.get("id") if result else None
        if item.run_id:
            invocation_id = db.get(Run, item.run_id).invocation_id
        if status == "succeeded" and result and invocation_id != item.completed_invocation_id:
            # A receipt can associate the newly created object and move private
            # navigation once. Business state is always re-read below.
            action = self.action_definition(
                definition, item.last_result["step_id"], item.active_action
            )
            values = {
                "completed_invocation_id": invocation_id,
                "revision": item.revision + 1,
                "updated_at": now(),
            }
            if action.get("creates_object"):
                ref = result.get("data", {}).get("projection", {}).get("object_ref")
                require(
                    isinstance(ref, dict)
                    and ref.get("object_type") == definition["provider"]["object_type"]
                    and isinstance(ref.get("object_id"), str),
                    "flow_result",
                    "创建结果缺少合法业务对象引用。",
                    502,
                )
                values["object_ref"] = deepcopy(ref)
            if action.get("target_step"):
                values["cursor"] = action["target_step"]
            changed = db.execute(
                update(WorkflowInstance)
                .where(WorkflowInstance.id == item.id, WorkflowInstance.revision == item.revision)
                .values(**values)
            )
            require(changed.rowcount == 1, "revision_conflict", "流程刚刚变化，请刷新。", 409)
            db.expire(item)
        return status, error

    async def get(self, actor, instance_id):
        """恢复本人流程并重新读取模块对象；正式状态始终来自业务服务。"""
        with self.runtime.sessions.begin() as db:
            item, definition = self.load(db, actor, instance_id)
            status, error = self._adopt_result(db, item, definition, actor)
            value = {
                key: deepcopy(getattr(item, key))
                for key in (
                    "id",
                    "flow_id",
                    "flow_version",
                    "cursor",
                    "drafts",
                    "revision",
                    "object_ref",
                    "conversation_id",
                    "forked_from",
                    "run_id",
                    "updated_at",
                )
            }
        provider = definition["provider"]
        projection = None
        if value["object_ref"]:
            projection = (
                await self.transport.request(
                    actor,
                    provider["read_capability"],
                    "objects.read",
                    {"object_ref": value["object_ref"]},
                )
            )["projection"]
        objects, pages = [], []
        for object_type in self.object_types(provider):
            page = await self.transport.request(
                actor,
                provider["read_capability"],
                "objects.list",
                {"object_type": object_type, "limit": 20},
            )
            objects.extend(page["objects"])
            pages.append({"object_type": object_type, "next_cursor": page.get("next_cursor")})
        # Deny stale authorization and a concurrent object switch after IO.
        with self.runtime.sessions() as db:
            current, _ = self.load(db, actor, instance_id)
            require(
                current.revision == value["revision"],
                "revision_conflict",
                "另一入口已经更新，请重新读取。",
                409,
            )
        step_actions = {
            step["id"]: self.actions(step, actor, projection, compact=True)
            for step in definition["steps"]
        }
        response = {
            **value,
            "title": definition["title"],
            "status": status,
            "error": error,
            "projection": projection,
            "objects": objects,
            "object_pages": pages,
            "step_actions": step_actions,
            "actions": step_actions[value["cursor"]],
        }
        require(
            len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= MAX_JSON_RESPONSE_BYTES,
            "workflow_view_size",
            "流程响应超过 2 MiB；请模块缩减投影与目录摘要，并通过详情分页查询。",
            502,
        )
        return response

    @staticmethod
    def object_types(provider):
        return list(
            dict.fromkeys([provider["object_type"], *provider.get("related_object_types", [])])
        )

    async def object_list(self, actor, instance_id, object_type, cursor=None, limit=20):
        payload = {"object_type": object_type, "limit": limit}
        if cursor is not None:
            payload["cursor"] = cursor
        validate_page_input("objects.list", payload)
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            revision, provider = item.revision, definition["provider"]
            require(
                object_type in self.object_types(provider),
                "flow_object_type",
                "此流程未声明该对象类型。",
                403,
            )
        page = await self.transport.request(
            actor, provider["read_capability"], "objects.list", payload
        )
        with self.runtime.sessions() as db:
            current, _ = self.load(db, actor, instance_id)
            require(
                current.revision == revision,
                "revision_conflict",
                "另一入口已经更新，请重新读取。",
                409,
            )
        return {**page, "next_cursor": page.get("next_cursor")}

    async def detail(self, actor, instance_id, section, cursor=None, limit=20):
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            require(item.object_ref is not None, "flow_object", "请先选择业务对象。", 422)
            revision, provider = item.revision, definition["provider"]
            payload = {"object_ref": deepcopy(item.object_ref), "section": section, "limit": limit}
        if cursor is not None:
            payload["cursor"] = cursor
        validate_page_input("objects.detail", payload)
        page = await self.transport.request(
            actor, provider["read_capability"], "objects.detail", payload
        )
        with self.runtime.sessions() as db:
            current, _ = self.load(db, actor, instance_id)
            require(
                current.revision == revision,
                "revision_conflict",
                "另一入口已经更新，请重新读取。",
                409,
            )
        return page

    def list(self, actor):
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            items = []
            for item in (
                db.query(WorkflowInstance)
                .filter_by(owner_id=actor["id"])
                .order_by(WorkflowInstance.updated_at.desc())
                .limit(100)
            ):
                try:
                    item, definition = self.load(db, actor, item.id)
                    items.append(
                        {
                            "id": item.id,
                            "flow_id": item.flow_id,
                            "title": definition["title"],
                            "updated_at": item.updated_at,
                        }
                    )
                except PlatformError:
                    continue
            return {"items": items}

    @staticmethod
    def editable(db, item):
        if item.run_id:
            run = db.get(Run, item.run_id)
            require(
                run is not None and run.status not in ("queued", "running"),
                "flow_running",
                "命令尚未返回，请等待回执。",
                409,
            )

    async def patch(self, actor, instance_id, cursor, drafts, revision):
        self.safe_draft(drafts)
        with self.runtime.sessions.begin() as db:
            item, definition = self.load(db, actor, instance_id)
            self.editable(db, item)
            self.step(definition, cursor)
            require(
                all(key in {s["id"] for s in definition["steps"]} for key in drafts),
                "flow_draft",
                "草稿包含未声明步骤。",
                422,
            )
            changed = db.execute(
                update(WorkflowInstance)
                .where(WorkflowInstance.id == item.id, WorkflowInstance.revision == revision)
                .values(
                    cursor=cursor, drafts=deepcopy(drafts), revision=revision + 1, updated_at=now()
                )
            )
            require(
                changed.rowcount == 1,
                "revision_conflict",
                "另一入口已修改个人草稿，请重新读取。",
                409,
            )
            audit(db, actor["id"], "workflow.draft", item.id, revision=revision + 1)
        return await self.get(actor, instance_id)

    async def select(self, actor, instance_id, object_id, revision):
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            self.editable(db, item)
            require(
                item.revision == revision, "revision_conflict", "流程已经变化，请重新读取。", 409
            )
        ref = (
            {"object_type": definition["provider"]["object_type"], "object_id": object_id}
            if object_id
            else None
        )
        if ref:
            await self.transport.request(
                actor,
                definition["provider"]["read_capability"],
                "objects.read",
                {"object_ref": ref},
            )
        with self.runtime.sessions.begin() as db:
            item, _ = self.load(db, actor, instance_id)
            self.editable(db, item)
            changed = db.execute(
                update(WorkflowInstance)
                .where(WorkflowInstance.id == item.id, WorkflowInstance.revision == revision)
                .values(
                    object_ref=ref,
                    drafts={},
                    run_id=None,
                    active_action=None,
                    last_result=None,
                    revision=revision + 1,
                    updated_at=now(),
                )
            )
            require(changed.rowcount == 1, "revision_conflict", "流程已经变化，请重新读取。", 409)
            audit(db, actor["id"], "workflow.select", item.id, object_ref=ref)
        return await self.get(actor, instance_id)

    async def action(self, actor, instance_id, action_id, command, revision, object_revision, key):
        """把已声明动作转成统一调用，携带个人修订和业务对象修订。"""
        guard = {
            "instance_id": instance_id,
            "action_id": action_id,
            "expected_revision": revision,
            "expected_object_revision": object_revision,
            "command_hash": digest(command),
        }
        invocation_key = "wf-" + digest({"instance": instance_id, "key": key})
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            existing = (
                db.query(Invocation)
                .filter_by(actor_id=actor["id"], idempotency_key=invocation_key)
                .first()
            )
            if existing:
                # A successful action may already have moved the cursor, and
                # page-to-chat attachment may have changed the current host.
                # Replays refer to the original receipt, never the new step or
                # selected object. Runtime still checks the complete original
                # guard fingerprint, current capability and actor permission.
                declared = any(
                    a["id"] == action_id and a["capability_id"] == existing.capability_id
                    for step in definition["steps"]
                    for a in step["actions"]
                )
                require(
                    declared and digest(existing.input.get("command")) == digest(command),
                    "idempotency_conflict",
                    "请求编号已用于另一项流程命令。",
                    409,
                )
                payload = deepcopy(existing.input)
                capability_id, conversation_id, origin = (
                    existing.capability_id,
                    existing.conversation_id,
                    existing.origin,
                )
            else:
                action = self.action_definition(definition, item.cursor, action_id)
                self.runtime.interactions.validate_safe(action["command_schema"], command)
                is_query = self.draft_query(
                    action, self.runtime.registry.get(action["capability_id"], actor)
                )
                payload = {"command": deepcopy(command)}
                if not action.get("creates_object") and not is_query:
                    require(
                        item.object_ref is not None
                        and type(object_revision) is int
                        and object_revision > 0,
                        "flow_object",
                        "请先选择业务对象并刷新。",
                        422,
                    )
                    payload.update(
                        object_id=item.object_ref["object_id"], expected_revision=object_revision
                    )
                capability_id, conversation_id = action["capability_id"], item.conversation_id
                origin = "conversation" if conversation_id else "page"
        result = await self.runtime.invoke(
            actor,
            capability_id,
            payload,
            invocation_key,
            origin,
            conversation_id,
            workflow_guard=guard,
        )
        return {"instance": await self.get(actor, instance_id), "result": result}

    def begin_action(self, db, actor, guard, spec, payload):
        """提交前再次核对动作、输入与授权，阻止旧页面覆盖新版本。"""
        item, definition = self.load(db, actor, guard["instance_id"])
        self.editable(db, item)
        action = self.action_definition(definition, item.cursor, guard["action_id"])
        require(
            action["capability_id"] == spec["id"]
            and action["capability_version"] == spec["version"]
            and digest(payload["command"]) == guard["command_hash"],
            "flow_changed",
            "动作定义或命令已经变化。",
            409,
        )
        is_query = self.draft_query(action, spec)
        if not action.get("creates_object") and not is_query:
            require(
                item.object_ref is not None
                and payload["object_id"] == item.object_ref["object_id"]
                and payload["expected_revision"] == guard["expected_object_revision"],
                "flow_changed",
                "业务对象引用已经变化。",
                409,
            )
        changed = db.execute(
            update(WorkflowInstance)
            .where(
                WorkflowInstance.id == item.id,
                WorkflowInstance.revision == guard["expected_revision"],
            )
            .values(revision=guard["expected_revision"] + 1, updated_at=now())
        )
        require(changed.rowcount == 1, "revision_conflict", "流程已经变化，请重新读取后提交。", 409)

    def finish_action(self, db, actor, guard, result):
        """保存调用回执引用及导航结果；正式业务状态由模块返回。"""
        item, _ = self.load(db, actor, guard["instance_id"])
        item.run_id = result.get("run_id")
        item.active_action = guard["action_id"]
        item.last_result = {"invocation_id": result["id"], "step_id": item.cursor}
        item.updated_at = now()
        audit(
            db,
            actor["id"],
            "workflow.command",
            item.id,
            action_id=guard["action_id"],
            invocation_id=result["id"],
        )

    async def resolve_transfer(self, instance_id, actor, operation):
        with self.runtime.sessions() as db:
            item, definition = self.load(db, actor, instance_id)
            require(item.object_ref is not None, "flow_object", "请先选择业务对象。", 422)
            provider_key = (
                "upload_capability" if operation.startswith("uploads.") else "jobs_capability"
            )
            capability = definition["provider"].get(provider_key)
            require(
                capability is not None,
                "flow_adapter",
                "此流程没有声明对应的文件或任务适配器。",
                422,
            )
            return capability, deepcopy(item.object_ref)

    def attach(self, actor, instance_id):
        with self.runtime.sessions.begin() as db:
            item, definition = self.load(db, actor, instance_id)
            if not item.conversation_id:
                conversation = Conversation(owner_id=actor["id"], title=definition["title"])
                db.add(conversation)
                db.flush()
                changed = db.execute(
                    update(WorkflowInstance)
                    .where(
                        WorkflowInstance.id == item.id,
                        WorkflowInstance.revision == item.revision,
                        WorkflowInstance.conversation_id.is_(None),
                    )
                    .values(
                        conversation_id=conversation.id,
                        revision=item.revision + 1,
                        updated_at=now(),
                    )
                )
                require(changed.rowcount == 1, "revision_conflict", "流程刚刚变化，请刷新。", 409)
                db.expire(item)
            if (
                not db.query(Message)
                .filter_by(conversation_id=item.conversation_id, request_key="flow-" + item.id)
                .first()
            ):
                db.add(
                    Message(
                        conversation_id=item.conversation_id,
                        role="assistant",
                        content="已继续当前业务对象。下方显示当前步骤；可以切换步骤，业务状态以服务最新记录为准。",
                        views=[self.view(item)],
                        request_key="flow-" + item.id,
                    )
                )
            return {"conversation_id": item.conversation_id, "instance_id": item.id}

    def clone_view(self, db, actor, view, conversation_id, mapping):
        """复制私人草稿和对象引用；分支创建本身不重放正式业务动作。"""
        original = view["data"]["instance_id"]
        if original not in mapping:
            source, _ = self.load(db, actor, original)
            clone, _ = self.create_in(db, actor, source.flow_id, "fork-" + uid(), conversation_id)
            clone.object_ref, clone.drafts, clone.cursor = (
                deepcopy(source.object_ref),
                deepcopy(source.drafts),
                source.cursor,
            )
            clone.forked_from = source.id
            db.flush()
            mapping[original] = clone
        return self.view(mapping[original])

    def open_capability(self, ctx, payload):
        invocation = ctx.session.get(Invocation, ctx.invocation_id)
        item, _ = self.create_in(
            ctx.session,
            ctx.actor,
            payload["flow_id"],
            "open-" + ctx.invocation_id,
            invocation.conversation_id,
        )
        return CapabilityOutput(
            "已打开流程步骤，尚未执行业务命令。", {"instance_id": item.id}, views=[self.view(item)]
        )

    def navigate_in(self, db, actor, instance_id, step_id, conversation_id):
        item, definition = self.load(db, actor, instance_id)
        require(
            item.conversation_id == conversation_id,
            "flow_conversation",
            "流程不属于当前对话。",
            403,
        )
        self.editable(db, item)
        step = self.step(definition, step_id)
        changed = db.execute(
            update(WorkflowInstance)
            .where(WorkflowInstance.id == item.id, WorkflowInstance.revision == item.revision)
            .values(cursor=step_id, revision=item.revision + 1, updated_at=now())
        )
        require(changed.rowcount == 1, "revision_conflict", "流程刚刚变化，请重试导引。", 409)
        db.expire(item)
        audit(db, actor["id"], "workflow.navigate", item.id, step_id=step_id)
        return item, step

    def navigate_capability(self, ctx, payload):
        invocation = ctx.session.get(Invocation, ctx.invocation_id)
        item, step = self.navigate_in(
            ctx.session,
            ctx.actor,
            payload["instance_id"],
            payload["step_id"],
            invocation.conversation_id,
        )
        return CapabilityOutput(
            step["description"], {"instance_id": item.id}, views=[self.view(item)]
        )

    def guidance(self, actor, conversation_id, text):
        catalog = self.catalog(actor)["items"]
        flow_id = text.split(" ", 1)[1].strip() if text.startswith("/flow ") else None
        matches = [d for d in catalog if text in [d["title"], *d.get("triggers", [])]]
        if not flow_id and len(matches) == 1:
            flow_id = matches[0]["id"]
        if flow_id:
            if flow_id not in {d["id"] for d in catalog}:
                return {"content": "此流程未安装或当前身份不可用。", "views": []}
            definition = next(value for value in catalog if value["id"] == flow_id)
            return {
                "content": self.step(definition, definition["initial_step"])["description"],
                "views": [{"type": "workflow-request", "data": {"flow_id": flow_id}}],
            }
        with self.runtime.sessions() as db:
            recent = (
                db.query(WorkflowInstance)
                .filter_by(owner_id=actor["id"], conversation_id=conversation_id)
                .order_by(WorkflowInstance.updated_at.desc())
                .first()
            )
            if recent:
                item, definition = self.load(db, actor, recent.id)
                steps = [
                    s
                    for s in definition["steps"]
                    if text in [s["title"], *s.get("triggers", [])] or text == "/step " + s["id"]
                ]
                if len(steps) == 1:
                    return {
                        "content": steps[0]["description"],
                        "views": [
                            {
                                "type": "workflow-navigation-request",
                                "data": {"instance_id": item.id, "step_id": steps[0]["id"]},
                            }
                        ],
                    }
        return None
