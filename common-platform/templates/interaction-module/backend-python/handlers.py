from app.contracts import CapabilityOutput


def echo(ctx, payload):
    # ctx.actor is the authenticated platform actor. Apply object-level scope
    # checks here when replacing the echo with real business data.
    return CapabilityOutput('已完成回显。', {'text': payload['message']})
