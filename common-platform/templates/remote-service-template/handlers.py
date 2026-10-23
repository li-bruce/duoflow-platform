"""Replace this pure handler with your service's implementation."""


def handle(capability_id: str, payload: dict, context: dict) -> tuple[str, dict]:
    return "独立服务已返回输入的消息。", {"text": payload["message"]}
