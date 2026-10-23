"""Publication state for controlled local packages and activation generations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .errors import PlatformError, require


class ModuleState:
    def __init__(self, directory: Path | None = None):
        self.path = Path(directory) / "module-state.json" if directory else None
        self.state = {}
        if self.path and self.path.exists():
            try:
                state = json.loads(self.path.read_text("utf-8"))
                if not isinstance(state, dict):
                    raise ValueError()
                for key, value in state.items():
                    if (
                        not isinstance(key, str)
                        or not isinstance(value, dict)
                        or set(value) != {"enabled", "revision"}
                    ):
                        raise ValueError()
                    if (
                        not isinstance(value["enabled"], bool)
                        or type(value["revision"]) is not int
                        or value["revision"] < 1
                    ):
                        raise ValueError()
                self.state = state
            except (ValueError, OSError, TypeError):
                raise PlatformError(
                    "module_state_invalid", "模块发布记录无法读取，请检查本机数据目录。", 500
                ) from None

    def get(self, module_id):
        return dict(self.state.get(module_id, {"enabled": True, "revision": 1}))

    def change(self, module_id, enabled, expected_revision):
        require(
            type(enabled) is bool and type(expected_revision) is int and expected_revision >= 1,
            "module_state_input",
            "模块发布状态或修订号无效。",
            422,
        )
        current = self.get(module_id)
        require(
            current["revision"] == expected_revision,
            "module_revision_conflict",
            "模块已发生变化，请刷新后重试。",
            409,
        )
        if current["enabled"] == enabled:
            return current
        value = {"enabled": enabled, "revision": current["revision"] + 1}
        updated = {**self.state, module_id: value}
        if self.path:
            name = None
            try:
                fd, name = tempfile.mkstemp(prefix=".module-state-", dir=self.path.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(updated, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
            except OSError:
                raise PlatformError(
                    "module_state_write", "模块发布记录保存失败，未发布新状态。", 500
                ) from None
            finally:
                if name and os.path.exists(name):
                    os.unlink(name)
        self.state = updated
        return value
