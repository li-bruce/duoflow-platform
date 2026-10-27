"""Explicit local exercise: configure reads private stdin; test really calls API."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("ai_service", Path(__file__).parents[2] / "sdk/python/ai_service.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
services = [dict(id="example.ai.analysis", title="独立合成分析", description="显式连接测试",
                 capability_ids=["example.ai.analyze"], supported_sources=["platform", "module"])]
try:
    if not os.environ.get("MODULE_AI_DATA_DIR"):
        raise sdk.AIServiceError("ai_private_directory_required", 500)
    ai = sdk.AIServiceManager(services=services, data_dir=os.environ["MODULE_AI_DATA_DIR"])
    action, service_id = sys.argv[1] if len(sys.argv) > 1 else "status", services[0]["id"]
    if action == "configure":
        raw = sys.stdin.read(32769)
        if len(raw.encode()) > 32768:
            raise sdk.AIServiceError("ai_configuration", 422)
        output = ai.put_config(service_id, expected_revision=ai.get_config(service_id)["revision"], profile=json.loads(raw))
    elif action == "test":
        output = asyncio.run(ai.test_connection(service_id, ai.get_config(service_id)["revision"]))
    elif action == "status":
        output = ai.get_config(service_id)
    else:
        raise sdk.AIServiceError("ai_example_action", 422)
    print(json.dumps(output, ensure_ascii=False))
except Exception as error:
    print(json.dumps(dict(error=error.code if isinstance(error, sdk.AIServiceError) else "ai_example_failed")), file=sys.stderr)
    sys.exit(1)
