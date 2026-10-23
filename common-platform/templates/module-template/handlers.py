"""Copyable extension handler: no router, database, identity, or demo dependency."""

from app.contracts import CapabilityContext, CapabilityOutput


async def echo(ctx: CapabilityContext, payload: dict) -> CapabilityOutput:
    data = {"echo": payload["message"]}
    return CapabilityOutput(
        summary="已原样返回消息。",
        data=data,
        views=[{"type": "text-result", "data": {"text": data["echo"]}}],
    )
