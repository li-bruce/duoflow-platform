"""An accepted action must not be reported as rejected by a racing view read."""

from sqlalchemy import select, func
from app.db import Invocation, WorkflowInstance
from test_framework_lab import host, open_flow, body, BASE


def test_accepted_action_rereads_after_concurrent_private_view_update(host, monkeypatch):
    item = open_flow(host)
    original = host.runtime.workflows.transport.request
    raced = False

    async def transport(actor, capability, operation, payload, *args, **kwargs):
        nonlocal raced
        result = await original(actor, capability, operation, payload, *args, **kwargs)
        if operation == "objects.list" and not raced:
            raced = True
            # Another legitimate view update races the post-command read. Use
            # the real patch API, not a fabricated successful transport result.
            with host.sessions() as db:
                current = db.get(WorkflowInstance, item["id"])
                revision, cursor, drafts = current.revision, current.cursor, current.drafts
            await host.runtime.workflows.patch(actor, item["id"], cursor, drafts, revision)
        return result

    monkeypatch.setattr(host.runtime.workflows.transport, "request", transport)
    response = host.client.post(
        f"{BASE}/instances/{item['id']}/actions/create",
        json=body(item, {"title": "Accepted once", "body": "Receipt recovery"}, "accepted-once"),
    )
    assert response.status_code == 200, response.text
    assert raced
    assert response.json()["instance"]["projection"]["state"] == "draft"
    with host.sessions() as db:
        count = db.scalar(
            select(func.count())
            .select_from(Invocation)
            .where(Invocation.capability_id == "framework-lab.create")
        )
    assert count == 1
