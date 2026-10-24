"""Persisted sequential execution over the platform's existing call/host APIs.

Only this coordinator advances composition steps. Business actions still pass
through Runtime.invoke; formal workflow completion comes from fresh projections.
An interrupted command is reconciled by receipt, never silently sent again.
"""

import asyncio
import logging

from .composition_models import CompositionRun
from .compositions import error_value
from .db import Invocation, Run, uid
from .errors import PlatformError, require
from .identity import load_actor

ACTIVE = {"ready", "waiting", "waiting_run", "opening", "waiting_service"}
STOPPED = {"succeeded", "failed", "blocked", "unknown"}
TEMPORARY_READ_FAILURES = {
    "remote_offline",
    "remote_stale",
    "remote_connection_failed",
    "remote_timeout",
    "workflow_unavailable",
    "workflow_connection",
    "workflow_timeout",
}
logger = logging.getLogger(__name__)


class CompositionExecution:
    def __init__(self, service):
        self.service = service
        self.runtime = service.runtime

    @staticmethod
    def request_key(snapshot, step):
        return "compose-" + snapshot["id"] + "-" + step["id"]

    def receipt(self, actor, snapshot, step):
        """Read the normal invocation receipt with its original authorization stamp."""
        with self.runtime.sessions() as db:
            row = (
                db.query(Invocation)
                .filter_by(actor_id=actor["id"], idempotency_key=self.request_key(snapshot, step))
                .first()
            )
            if row is None:
                return None
            self.runtime.check_invocation_authorization(row, actor)
            result = row.result
            if result and result.get("run_id"):
                job = self.runtime.current_run_view(db, db.get(Run, result["run_id"]), actor)
                require(
                    not job.get("error") or job["status"] in {"failed", "cancelled"},
                    "authorization_changed",
                    "调用授权已变化。",
                    403,
                )
                return {
                    "id": row.id,
                    "status": job["status"],
                    "result": job.get("result"),
                    "error": job.get("error"),
                }
            return {
                "id": row.id,
                "status": "succeeded" if row.status == "completed" else "running",
                "result": result,
            }

    async def advance(self, actor, run_id, *, revision=None, confirm_step=None, reconcile=False):
        """Advance until an external wait, explicit confirmation, failure or completion."""
        async with self.service.lock:
            snapshot = self.service.load(actor, run_id)
            require(
                revision is None or revision == snapshot["revision"],
                "revision_conflict",
                "组合已更新，请刷新后操作。",
                409,
            )
            if snapshot["status"] in STOPPED and not (
                reconcile and snapshot["status"] == "unknown"
            ):
                return await self.service.read(actor, run_id)
            if snapshot["status"] == "dispatching" and not reconcile:
                return await self.service.read(actor, run_id)
            try:
                self.service.check_pins(actor, snapshot)
                # Each pass completes at most one of the bounded declared steps.
                for _ in range(len(snapshot["steps"]) + 1):
                    snapshot = self.service.load(actor, run_id)
                    self.service.check_pins(actor, snapshot)
                    step = next((s for s in snapshot["steps"] if s["status"] != "succeeded"), None)
                    if step is None:
                        self.service.transition(snapshot, run_status="succeeded")
                        break
                    definition = snapshot["document"]["steps"][step["position"]]
                    flow = snapshot["pins"][step["step_id"]]["flow"]
                    if step["status"] in {"dispatching", "waiting_run", "unknown"}:
                        if await self.resume_receipt(actor, snapshot, step, reconcile):
                            continue
                        break
                    if step["status"] == "waiting":
                        if await self.observe_workflow(actor, snapshot, step, flow):
                            continue
                        break
                    if step["status"] not in {"pending", "confirming", "opening"}:
                        break
                    values = await self.service.inputs(actor, snapshot, definition)
                    if step["input"] is not None:
                        require(
                            step["input"] == values,
                            "composition_source_changed",
                            "等待期间来源内容已变化，请检查后重新开始。",
                            409,
                        )
                    entry = flow["entry"]
                    if entry["kind"] == "workflow":
                        await self.open_workflow(actor, snapshot, step, entry, values)
                        break
                    if not await self.dispatch_capability(
                        actor, snapshot, step, entry, values, confirm_step
                    ):
                        break
                    confirm_step = None
            except PlatformError as failure:
                if failure.code == "revision_conflict":
                    raise
                snapshot = self.service.load(actor, run_id)
                # This handler only covers observation and pre-dispatch checks.
                # Command failures have already been isolated as unknown above.
                status = "waiting_service" if failure.code in TEMPORARY_READ_FAILURES else "blocked"
                error = error_value(failure)
                if snapshot["status"] != status or snapshot["error"] != error:
                    self.service.transition(snapshot, run_status=status, error=error)
            return await self.service.read(actor, run_id)

    async def resume_receipt(self, actor, snapshot, step, reconcile):
        """Resolve an already dispatched call using only its original receipt."""
        receipt = self.receipt(actor, snapshot, step)
        if receipt is None:
            if reconcile:
                error = {
                    "code": "outcome_unknown",
                    "message": "平台尚未收到结果，已暂停后续步骤。请在模块中核对这次调用。",
                }
                self.service.transition(
                    snapshot,
                    step,
                    run_status="unknown",
                    status="unknown",
                    error=error,
                )
            return False
        if receipt["status"] in {"queued", "running"}:
            if step["status"] != "waiting_run" or snapshot["status"] == "waiting_service":
                self.service.transition(
                    snapshot,
                    step,
                    run_status="waiting_run",
                    status="waiting_run",
                    invocation_id=receipt["id"],
                )
            return False
        if receipt["status"] != "succeeded":
            # A host interruption describes receipt loss, not a remote rollback.
            # The module may already have committed its transaction.
            if (receipt.get("error") or {}).get("code") == "interrupted":
                error = {
                    "code": "outcome_unknown",
                    "message": "运行中断，平台未确认模块最终结果。已暂停后续步骤，请先在模块中核对这次调用。",
                }
                self.service.transition(
                    snapshot, step, run_status="unknown", status="unknown", error=error
                )
                return False
            error = receipt.get("error") or {
                "code": "call_failed",
                "message": "业务调用未完成，请查看调用记录。",
            }
            self.service.transition(
                snapshot, step, run_status="failed", status="failed", error=error
            )
            return False
        await self.finish(
            actor,
            snapshot,
            step,
            receipt["result"]["data"],
            invocation_id=receipt["id"],
        )
        return True

    async def observe_workflow(self, actor, snapshot, step, flow):
        """Wait for a fresh formal state; private navigation cannot finish a step."""
        item = await self.runtime.workflows.get(actor, step["instance_id"])
        projection = item.get("projection")
        if projection and projection["state"] in flow["entry"]["completed_states"]:
            await self.finish(actor, snapshot, step, {"projection": projection})
            return True
        if snapshot["status"] == "waiting_service":
            self.service.transition(snapshot, run_status="waiting")
        return False

    async def open_workflow(self, actor, snapshot, step, entry, values):
        """Open or restore the same personal host without invoking business commands."""
        if step["status"] != "opening":
            self.service.transition(
                snapshot, step, run_status="opening", status="opening", input=values
            )
            snapshot = self.service.load(actor, snapshot["id"])
            step = snapshot["steps"][step["position"]]
        item = await self.runtime.workflows.create(
            actor, entry["id"], self.request_key(snapshot, step)
        )
        if values.get("object_id") and not item["object_ref"]:
            item = await self.runtime.workflows.select(
                actor, item["id"], values["object_id"], item["revision"]
            )
        self.service.check_pins(actor, snapshot)
        self.service.transition(
            snapshot,
            step,
            run_status="waiting",
            status="waiting",
            instance_id=item["id"],
        )

    async def dispatch_capability(self, actor, snapshot, step, entry, values, confirm_step):
        """Confirm, persist and invoke one capability; uncertain writes stop here."""
        spec = self.runtime.registry.get(entry["id"], actor)
        # Check before saving a dispatch. Once a command is sent, an
        # uncertain outcome must be reconciled rather than retried.
        self.runtime.check_available(spec)
        if spec["execution"]["confirmation"] and confirm_step != step["step_id"]:
            if step["status"] != "confirming" or snapshot["status"] == "waiting_service":
                self.service.transition(
                    snapshot,
                    step,
                    run_status="confirming",
                    status="confirming",
                    input=values,
                )
            return False
        require(
            confirm_step is None or step["status"] == "confirming",
            "composition_confirmation",
            "请先查看当前步骤内容，再确认执行。",
            409,
        )
        token = uid()
        self.service.transition(
            snapshot,
            step,
            run_status="dispatching",
            status="dispatching",
            input=values,
            dispatch_token=token,
        )
        snapshot = self.service.load(actor, snapshot["id"])
        step = snapshot["steps"][step["position"]]
        guard = {"run_id": snapshot["id"], "step_id": step["step_id"], "token": token}
        try:
            result = await self.runtime.invoke(
                actor,
                entry["id"],
                values,
                self.request_key(snapshot, step),
                composition_guard=guard,
            )
        except Exception as failure:
            status = "unknown" if spec["kind"] == "command" else "failed"
            self.service.transition(
                snapshot,
                step,
                run_status=status,
                status=status,
                error=error_value(failure),
            )
            return False
        if result.get("run_id"):
            self.service.transition(
                snapshot,
                step,
                run_status="waiting_run",
                status="waiting_run",
                invocation_id=result["id"],
            )
            return False
        await self.finish(actor, snapshot, step, result["data"], invocation_id=result["id"])
        return True

    async def finish(self, actor, snapshot, step, output, **receipt):
        """Validate and save one result before allowing the next step to start."""
        flow = snapshot["pins"][step["step_id"]]["flow"]
        self.runtime.interactions.validate_safe(flow["output_schema"], output)
        self.service.check_pins(actor, snapshot)
        self.service.transition(
            snapshot, step, run_status="ready", status="succeeded", output=output, **receipt
        )

    async def worker(self):
        """Continue accepted runs while pages are closed; saved commands are not replayed."""
        while True:
            with self.runtime.sessions() as db:
                runs = [
                    (r.id, r.owner_id)
                    for r in db.query(CompositionRun)
                    .filter(CompositionRun.status.in_(ACTIVE))
                    .order_by(CompositionRun.updated_at)
                    .limit(50)
                ]
            for run_id, owner_id in runs:
                try:
                    with self.runtime.sessions() as db:
                        actor = load_actor(db, owner_id)
                    await self.advance(actor, run_id)
                except PlatformError:
                    # A concurrent request or disabled actor cannot authorize an effect.
                    continue
                except Exception as failure:
                    # Isolate a failed coordinator iteration. Preserve the saved receipt
                    # and retry observation later; never log payloads or credentials.
                    logger.error(
                        "Composition %s could not be observed (%s)", run_id, type(failure).__name__
                    )
            await asyncio.sleep(2)
