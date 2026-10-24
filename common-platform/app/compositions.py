"""Composition boundaries: versions, current authority, data provenance and storage.

Execution is in composition_execution.py; module state remains in the module.
Definitions and run inputs belong to one user. A role description never grants
that user access, and a saved output never substitutes for current object access.
"""

import asyncio
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from .composition_contract import SCHEMA, MISSING, at_path, validate_composition
from .composition_models import CompositionDefinition, CompositionRun, CompositionStep
from .db import Invocation, Message, audit, now
from .errors import PlatformError, require
from .identity import authorization_snapshot, has_permission, refresh_actor
from .registration_contract import digest


def error_value(error):
    """Only framework errors cross the public boundary; internal details stay out."""
    if isinstance(error, PlatformError):
        return {"code": error.code, "message": error.message}
    return {"code": "composition_error", "message": "这一步未能完成，请查看调用记录。"}


class Compositions:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.task = None
        from .composition_execution import CompositionExecution

        self.execution = CompositionExecution(self)
        from .composition_graph import CompositionGraph

        self.graph = CompositionGraph(self)

    def principal(self, actor):
        return {
            "identity": authorization_snapshot(actor),
            "access_stamp": self.runtime.access.authorization_stamp(actor),
        }

    def compile(self, actor, document):
        """Pin actual admitted exports and check every connection before saving."""
        self.runtime.interactions.validate_safe({"type": "object"}, document)
        require(
            Draft202012Validator(SCHEMA).is_valid(document),
            "composition_contract",
            "组合定义不符合当前格式。",
            422,
        )
        flows, pins, modules = {}, {}, {}
        for step in document["steps"]:
            module, flow = self.runtime.module_catalog.flow(
                actor, step["module_id"], step["flow_id"], step["flow_version"]
            )
            flows[step["id"]] = flow
            modules[module["module_id"]] = module
            pins[step["id"]] = {
                key: deepcopy(module[key])
                for key in (
                    "module_id",
                    "contract_digest",
                    "service_id",
                    "binding_revision",
                    "graph",
                )
            }
            pins[step["id"]]["flow"] = flow
            pins[step["id"]]["execution_revisions"] = {
                cap["id"]: self.runtime.execution_revision(
                    self.runtime.registry.capabilities[cap["id"]]
                )
                for cap in flow["capabilities"]
            }
        document = validate_composition(document, flows)
        for step in document["steps"]:
            for binding in step["bindings"].values():
                if binding["source"] != "step":
                    continue
                source = flows[binding["step_id"]]
                if source["data_objects"]:
                    require(
                        binding["path"][0] == "projection"
                        and all("read" in obj["operations"] for obj in source["data_objects"]),
                        "composition_provenance",
                        "业务对象数据连接需要明确的对象投影和读取接口。",
                        422,
                    )
        for role in document["roles"]:
            seen = set()
            for binding in role["bindings"]:
                from .access_control import BINDING_SCHEMA

                require(
                    Draft202012Validator(BINDING_SCHEMA).is_valid(
                        {"subject_id": actor["id"], **binding}
                    ),
                    "composition_role",
                    "模块角色的范围格式无效。",
                    422,
                )
                module = modules.get(binding["module_id"])
                require(
                    module is not None, "composition_role", "角色只能关联组合中使用的模块。", 422
                )
                access = module["registration"]["permissions"]["definition"] or {}
                target = next(
                    (r for r in access.get("role_templates", []) if r["id"] == binding["role_id"]),
                    None,
                )
                identity = digest(binding)
                require(
                    target is not None
                    and identity not in seen
                    and set(target["required_scope"])
                    <= binding["scope"].keys()
                    <= set(access["scope_keys"]),
                    "composition_role",
                    "角色或范围与模块声明不一致，或重复关联。",
                    422,
                )
                seen.add(identity)
        require(
            len(json.dumps(pins, ensure_ascii=False).encode("utf-8")) <= 2_000_000,
            "composition_size",
            "组合引用的业务图超过大小限制。",
            413,
        )
        return document, pins

    def check_pins(self, actor, snapshot):
        """Never silently upgrade a saved run to a changed service or contract."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
        require(
            self.principal(actor) == snapshot["principal"],
            "authorization_changed",
            "身份或授权范围已变化，需重新检查组合并开始新的办理。",
            403,
        )
        for pin in snapshot["pins"].values():
            module, _ = self.runtime.module_catalog.flow(
                actor,
                pin["module_id"],
                pin["flow"]["id"],
                pin["flow"]["version"],
                require_available=False,
            )
            require(
                all(
                    module[key] == pin[key]
                    for key in ("contract_digest", "service_id", "binding_revision")
                ),
                "composition_version_changed",
                "模块或服务连接已更新，请检查组合版本后重新开始。",
                409,
            )
            require(
                all(
                    identifier in self.runtime.registry.capabilities
                    and self.runtime.execution_revision(
                        self.runtime.registry.capabilities[identifier]
                    )
                    == revision
                    for identifier, revision in pin["execution_revisions"].items()
                ),
                "composition_version_changed",
                "模块启停或能力版本已经变化，请重新检查组合。",
                409,
            )

    def save(self, actor, document):
        """Save an immutable checked version, reusing only the identical definition."""
        document, pins = self.compile(actor, document)
        fingerprint = digest({"document": document, "pins": pins})
        try:
            with self.runtime.sessions.begin() as db:
                actor = refresh_actor(db, actor, require_unchanged=True)
                row = (
                    db.query(CompositionDefinition)
                    .filter_by(
                        owner_id=actor["id"], logical_id=document["id"], version=document["version"]
                    )
                    .first()
                )
                if row:
                    require(
                        row.fingerprint == fingerprint,
                        "composition_version_exists",
                        "这个版本已经保存，修改后请填写新的版本号。",
                        409,
                    )
                else:
                    row = CompositionDefinition(
                        owner_id=actor["id"],
                        logical_id=document["id"],
                        version=document["version"],
                        document=document,
                        pins=pins,
                        fingerprint=fingerprint,
                    )
                    db.add(row)
                    db.flush()
                    audit(db, actor["id"], "composition.save", row.id, version=document["version"])
                return {
                    "id": row.id,
                    "document": deepcopy(row.document),
                    "fingerprint": row.fingerprint,
                    "shared": row.shared,
                    "owned": True,
                }
        except IntegrityError:
            raise PlatformError(
                "composition_version_exists", "这个版本正在其他窗口保存，请刷新后查看。", 409
            ) from None

    def definitions(self, actor):
        """List owned or published definitions that the actor can currently enter."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            items = []
            for row in (
                db.query(CompositionDefinition)
                .filter(
                    or_(
                        CompositionDefinition.owner_id == actor["id"],
                        CompositionDefinition.shared == True,
                    )
                )
                .order_by(CompositionDefinition.created_at.desc())
                .limit(100)
            ):
                try:
                    self.definition(db, actor, row.id)
                except PlatformError:
                    continue
                items.append(
                    {
                        "id": row.id,
                        "document": deepcopy(row.document),
                        "fingerprint": row.fingerprint,
                        "shared": row.shared,
                        "owned": row.owner_id == actor["id"],
                    }
                )
            return {"items": items}

    def definition(self, db, actor, identifier):
        row = db.get(CompositionDefinition, identifier)
        require(
            row is not None and (row.owner_id == actor["id"] or row.shared),
            "composition_missing",
            "没有可访问的组合定义。",
            404,
        )
        if row.owner_id != actor["id"]:
            self.check_pins(actor, {"pins": row.pins, "principal": self.principal(actor)})
        return row

    def publish(self, actor, definition_id, shared):
        """Publish a reviewed definition to users who can enter every referenced module."""
        with self.runtime.sessions.begin() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
            require(
                has_permission(actor, "identity.manage"),
                "forbidden",
                "发布组合需要平台管理权限。",
                403,
            )
            row = self.definition(db, actor, definition_id)
            require(row.owner_id == actor["id"], "forbidden", "只能发布自己保存的组合定义。", 403)
            if shared:
                self.check_pins(actor, {"pins": row.pins, "principal": self.principal(actor)})
            row.shared = shared
            audit(
                db,
                actor["id"],
                "composition.publish" if shared else "composition.unpublish",
                row.id,
            )
            return {"shared": shared}

    def start_run(self, actor, definition_id, payload, key, conversation_id=None):
        """Persist all pending steps before the first call can leave this process."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
            definition = self.definition(db, actor, definition_id)
            document, pins = deepcopy(definition.document), deepcopy(definition.pins)
        self.runtime.interactions.validate_safe(document["input_schema"], payload)
        principal = self.principal(actor)
        self.check_pins(actor, {"pins": pins, "principal": principal})
        fingerprint = digest(
            {"definition_id": definition_id, "input": payload, "conversation_id": conversation_id}
        )
        try:
            with self.runtime.sessions.begin() as db:
                actor = refresh_actor(db, actor, require_unchanged=True)
                if conversation_id:
                    self.runtime.conversation_check(db, conversation_id, actor)
                existing = (
                    db.query(CompositionRun)
                    .filter_by(owner_id=actor["id"], creation_key=key)
                    .first()
                )
                if existing:
                    require(
                        existing.creation_hash == fingerprint,
                        "idempotency_conflict",
                        "相同创建标识不能用于另一份组合输入。",
                        409,
                    )
                    return existing.id
                run = CompositionRun(
                    owner_id=actor["id"],
                    definition_id=definition_id,
                    principal=principal,
                    creation_key=key,
                    creation_hash=fingerprint,
                    input=deepcopy(payload),
                )
                db.add(run)
                db.flush()
                if conversation_id:
                    db.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=document["title"],
                            views=[{"type": "composition", "data": {"run_id": run.id}}],
                            request_key="composition-start-" + run.id,
                        )
                    )
                for index, step in enumerate(document["steps"]):
                    db.add(CompositionStep(run_id=run.id, step_id=step["id"], position=index))
                audit(db, actor["id"], "composition.start", run.id, definition_id=definition_id)
                return run.id
        except IntegrityError:
            raise PlatformError(
                "concurrent_request", "组合正在其他窗口创建，请重试原请求。", 409
            ) from None

    def load(self, actor, run_id):
        """Materialize owned metadata; authority over business contents is checked separately."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
            run = db.get(CompositionRun, run_id)
            require(
                run is not None and run.owner_id == actor["id"],
                "composition_missing",
                "没有可访问的组合运行。",
                404,
            )
            definition = db.get(CompositionDefinition, run.definition_id)
            require(definition is not None, "composition_missing", "组合定义不存在。", 404)
            result = {
                key: deepcopy(getattr(run, key))
                for key in (
                    "id",
                    "owner_id",
                    "definition_id",
                    "principal",
                    "input",
                    "status",
                    "revision",
                    "error",
                    "created_at",
                    "updated_at",
                )
            }
            result.update(document=deepcopy(definition.document), pins=deepcopy(definition.pins))
            result["steps"] = [
                {
                    key: deepcopy(getattr(step, key))
                    for key in (
                        "id",
                        "step_id",
                        "position",
                        "status",
                        "input",
                        "output",
                        "invocation_id",
                        "instance_id",
                        "dispatch_token",
                        "error",
                        "updated_at",
                    )
                }
                for step in db.query(CompositionStep)
                .filter_by(run_id=run_id)
                .order_by(CompositionStep.position)
            ]
            return result

    def transition(self, snapshot, step=None, *, run_status=None, error=None, **values):
        """Compare-and-swap both records; two processes cannot claim the same step."""
        with self.runtime.sessions.begin() as db:
            changed = db.execute(
                update(CompositionRun)
                .where(
                    CompositionRun.id == snapshot["id"],
                    CompositionRun.revision == snapshot["revision"],
                )
                .values(
                    revision=snapshot["revision"] + 1,
                    status=run_status or snapshot["status"],
                    error=error,
                    updated_at=now(),
                )
            )
            require(
                changed.rowcount == 1,
                "revision_conflict",
                "组合已在另一个窗口或后台继续，请刷新。",
                409,
            )
            if step:
                values["error"] = error
                db.execute(
                    update(CompositionStep)
                    .where(
                        CompositionStep.id == step["id"], CompositionStep.run_id == snapshot["id"]
                    )
                    .values(**deepcopy(values), updated_at=now())
                )

    async def authorized_output(self, actor, snapshot, step, *, unchanged=True):
        """Re-read object facts before transferring saved output or showing its content."""
        output = deepcopy(step["output"])
        require(
            output is not None and step["status"] == "succeeded",
            "composition_source_pending",
            "来源步骤尚未完成。",
            409,
        )
        flow = snapshot["pins"][step["step_id"]]["flow"]
        if flow["data_objects"]:
            projection = output.get("projection", {})
            ref = projection.get("object_ref", {})
            obj = next((o for o in flow["data_objects"] if o["id"] == ref.get("object_type")), None)
            require(
                obj and obj["operations"].get("read"),
                "composition_provenance",
                "业务输出缺少可重新授权的对象引用。",
                403,
            )
            fresh = await self.runtime.workflows.transport.request(
                actor, obj["operations"]["read"], "objects.read", {"object_ref": ref}
            )
            require(
                not unchanged or fresh["projection"] == projection,
                "composition_source_changed",
                "来源业务内容已变化，请检查后重新开始组合。",
                409,
            )
            return {"projection": deepcopy(fresh["projection"])}
        if step["invocation_id"]:
            with self.runtime.sessions() as db:
                invocation = db.get(Invocation, step["invocation_id"])
                self.runtime.check_invocation_authorization(invocation, actor)
        return output

    async def inputs(self, actor, snapshot, definition):
        """Assemble explicit inputs, preserving omission and reauthorizing every source."""
        values = {}
        outputs = {}
        schema = snapshot["pins"][definition["id"]]["flow"]["input_schema"]
        for field, binding in definition["bindings"].items():
            source = binding["source"]
            if source == "literal":
                value = deepcopy(binding["value"])
            elif source == "input":
                value = at_path(
                    snapshot["input"],
                    binding["path"],
                    optional=field not in schema.get("required", []),
                )
            else:
                identifier = binding["step_id"]
                if identifier not in outputs:
                    step = next(s for s in snapshot["steps"] if s["step_id"] == identifier)
                    outputs[identifier] = await self.authorized_output(actor, snapshot, step)
                value = at_path(
                    outputs[identifier],
                    binding["path"],
                    optional=field not in schema.get("required", []),
                )
            if value is not MISSING:
                values[field] = value
        self.runtime.interactions.validate_safe(schema, values)
        return values

    async def check_dispatch(self, actor, capability_id, payload, guard):
        """Called inside the normal executor immediately before sync/background effects."""
        snapshot = self.load(actor, guard["run_id"])
        self.check_pins(actor, snapshot)
        step = next((s for s in snapshot["steps"] if s["step_id"] == guard["step_id"]), None)
        require(
            step
            and step["status"] in {"dispatching", "waiting_run"}
            and step["dispatch_token"] == guard["token"],
            "composition_guard",
            "组合步骤已变更或停止。",
            409,
        )
        definition = snapshot["document"]["steps"][step["position"]]
        require(
            snapshot["pins"][step["step_id"]]["flow"]["entry"]["id"] == capability_id
            and step["input"] == payload == await self.inputs(actor, snapshot, definition),
            "composition_guard",
            "步骤的来源内容或调用输入已变化。",
            409,
        )
        self.check_pins(actor, snapshot)

    async def read(self, actor, run_id):
        """Restore owned progress and reauthorize contents before exposing any result."""
        snapshot = self.load(actor, run_id)
        error = None
        current_outputs = {}
        try:
            self.check_pins(actor, snapshot)
            for step in snapshot["steps"]:
                if step["status"] == "succeeded":
                    current_outputs[step["step_id"]] = await self.authorized_output(
                        actor, snapshot, step, unchanged=False
                    )
            self.check_pins(actor, snapshot)
        except PlatformError as failure:
            error = error_value(failure)
        steps = []
        for step in snapshot["steps"]:
            definition = snapshot["document"]["steps"][step["position"]]
            pin = snapshot["pins"][step["step_id"]]
            view = (
                {"type": "workflow", "data": {"instance_id": step["instance_id"]}}
                if step["instance_id"] and not error
                else None
            )
            steps.append(
                {
                    "id": step["step_id"],
                    "title": definition["title"],
                    "status": step["status"],
                    "module_id": pin["module_id"],
                    "flow_id": pin["flow"]["id"],
                    "input": {} if error else step["input"],
                    "output": None if error else current_outputs.get(step["step_id"]),
                    "invocation_id": None if error else step["invocation_id"],
                    "view": view,
                    "error": error or step["error"],
                }
            )
        return {
            key: snapshot[key]
            for key in ("id", "definition_id", "status", "revision", "created_at", "updated_at")
        } | {
            "title": snapshot["document"]["title"],
            "input": {} if error else snapshot["input"],
            "steps": steps,
            "error": error or snapshot["error"],
            "content_available": error is None,
        }

    def runs(self, actor):
        """List only the actor's recent run summaries; contents have a separate check."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor)
            items = []
            for row in (
                db.query(CompositionRun)
                .filter_by(owner_id=actor["id"])
                .order_by(CompositionRun.updated_at.desc())
                .limit(100)
            ):
                definition = db.get(CompositionDefinition, row.definition_id)
                items.append(
                    {
                        "id": row.id,
                        "title": definition.document["title"],
                        "status": row.status,
                        "updated_at": row.updated_at,
                    }
                )
            return {"items": items}

    def attach(self, actor, run_id):
        """Attach one saved run; switching surfaces creates no business effects."""
        snapshot = self.load(actor, run_id)
        self.check_pins(actor, snapshot)
        from .conversation import ConversationService

        conversation = ConversationService(self.runtime).create(actor)
        with self.runtime.sessions.begin() as db:
            db.add(
                Message(
                    conversation_id=conversation["id"],
                    role="assistant",
                    content=snapshot["document"]["title"],
                    views=[{"type": "composition", "data": {"run_id": run_id}}],
                    request_key="composition-" + run_id,
                )
            )
        return {"conversation_id": conversation["id"]}

    def guidance(self, actor, text):
        """Prepare a saved composition input form; interpreting text starts no run."""
        if not text.startswith("/compose "):
            return None
        identifier = text.removeprefix("/compose ").strip()
        with self.runtime.sessions() as db:
            row = self.definition(db, actor, identifier)
            self.compile(actor, row.document)
            return {
                "content": row.document["description"],
                "views": [{"type": "composition-start", "data": {"definition_id": row.id}}],
            }

    def grant_role(self, actor, definition_id, role_id, subject_id, expires_at=None):
        """An administrator applies the explicit mappings as ordinary module grants."""
        with self.runtime.sessions() as db:
            actor = refresh_actor(db, actor, require_unchanged=True)
            require(
                has_permission(actor, "identity.manage"),
                "forbidden",
                "分配组合角色需要身份管理权限。",
                403,
            )
            row = self.definition(db, actor, definition_id)
            document, pins = deepcopy(row.document), deepcopy(row.pins)
        self.compile(actor, document)
        self.check_pins(actor, {"pins": pins, "principal": self.principal(actor)})
        role = next((r for r in document["roles"] if r["id"] == role_id), None)
        require(role is not None, "composition_role", "组合中没有这个角色。", 404)
        return self.runtime.access.grant_many(
            subject_id, role["bindings"], actor_id=actor["id"], expires_at=expires_at
        )

    async def start(self):
        self.task = asyncio.create_task(self.execution.worker())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
