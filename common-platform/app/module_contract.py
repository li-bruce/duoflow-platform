"""Shared page/conversation declarations for local and remote module manifests.

Remote manifest protocol 1.1 adds these declarations. Its invocation envelope is
still protocol 1.0: presentation registration and invocation are versioned apart.
Only structured metadata is accepted; this contract never executes HTML or code.
"""

from __future__ import annotations

import copy
import re


def normalize_surfaces(
    module_id, capabilities, surfaces=None, allow_embedded=False, allow_skills=False
) -> dict:
    """Validate complete dual-surface coverage or derive forms for a legacy module.

    Each capability occurs on at least one page and exactly once in conversation.
    A capability can back several tools, but cannot repeat within one tool.
    ``embedded`` is remote-only and stores a same-service absolute path, never a
    URL; the caller concatenates it with the administered base URL (including
    any configured base path).
    Errors intentionally contain no submitted strings or manifest values.
    """

    def invalid(message="模块的页面与对话声明无效。"):
        raise ValueError(message)

    def text(value, limit, *, empty=False):
        if (
            not isinstance(value, str)
            or len(value) > limit
            or (not empty and not value.strip())
            or any(ord(c) < 32 for c in value)
        ):
            invalid()
        return value

    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,120}", value):
            invalid("模块或工具标识格式无效。")
        return value

    def exact_fields(value, required, optional=()):
        if (
            not isinstance(value, dict)
            or set(required) - set(value)
            or set(value) - set(required) - set(optional)
        ):
            invalid()

    identifier(module_id)
    if not isinstance(capabilities, list) or not capabilities:
        invalid("模块需要声明可供两端使用的能力。")
    all_ids = []
    for cap in capabilities:
        if not isinstance(cap, dict):
            invalid()
        cap_id = identifier(cap.get("id"))
        if cap_id in all_ids:
            invalid("能力标识不能重复。")
        all_ids.append(cap_id)
    known = set(all_ids)
    if surfaces is None:
        return {
            "page_tools": [
                {
                    "id": cap["id"],
                    "title": text(cap.get("title", cap["id"]), 200),
                    "description": text(cap.get("description", ""), 3000, empty=True),
                    "kind": "form",
                    "capability_ids": [cap["id"]],
                }
                for cap in capabilities
            ],
            "conversation": {"capability_ids": all_ids, "starter_prompts": []},
        }

    exact_fields(surfaces, {"page_tools", "conversation"})
    page_tools = surfaces["page_tools"]
    if not isinstance(page_tools, list) or not 1 <= len(page_tools) <= 200:
        invalid("模块需要至少一个页面工具。")
    tool_ids, page_ids = set(), []
    for tool in page_tools:
        exact_fields(tool, {"id", "title", "description", "kind", "capability_ids"}, {"path"})
        tool_id = identifier(tool["id"])
        if tool_id in tool_ids:
            invalid("页面工具标识不能重复。")
        tool_ids.add(tool_id)
        text(tool["title"], 200)
        text(tool["description"], 3000, empty=True)
        ids = tool["capability_ids"]
        if not isinstance(ids, list) or not ids:
            invalid("每个页面工具都需要绑定能力。")
        tool_capabilities = set()
        for cap_id in ids:
            if not isinstance(cap_id, str) or cap_id not in known or cap_id in tool_capabilities:
                invalid("页面声明包含未知或重复的能力标识。")
            tool_capabilities.add(cap_id)
            page_ids.append(cap_id)
        if tool["kind"] == "form":
            if "path" in tool:
                invalid("表单工具不接受外部页面路径。")
        elif tool["kind"] == "embedded":
            if not allow_embedded:
                invalid("本地模块不能声明嵌入远程页面。")
            path = tool.get("path")
            if (
                not isinstance(path, str)
                or len(path) > 1024
                or not path.startswith("/")
                or "//" in path
                or not re.fullmatch(r"/[A-Za-z0-9/_.~-]*", path)
                or any(segment in {".", ".."} for segment in path.split("/"))
            ):
                invalid("嵌入页面只能使用同源绝对路径，不能包含转义、跳转或查询片段。")
        else:
            invalid("页面工具类型无效。")
    if set(page_ids) != known:
        invalid("页面工具必须覆盖模块的全部能力。")
    conversation = surfaces["conversation"]
    exact_fields(
        conversation, {"capability_ids", "starter_prompts"}, {"skills"} if allow_skills else ()
    )
    ids = conversation["capability_ids"]
    if (
        not isinstance(ids, list)
        or any(not isinstance(cap_id, str) for cap_id in ids)
        or len(ids) != len(set(ids))
        or set(ids) != known
    ):
        invalid("对话声明必须完整覆盖能力，且不能含未知或重复标识。")
    prompts = conversation["starter_prompts"]
    if not isinstance(prompts, list) or len(prompts) > 20:
        invalid("对话引导语格式无效。")
    for prompt in prompts:
        text(prompt, 1000)
    if "skills" in conversation:
        from .workspace_contract import normalize_skills
        from .errors import PlatformError

        try:
            normalize_skills(conversation["skills"], capabilities)
        except PlatformError:
            invalid("对话流程声明不符合技能契约。")
    return copy.deepcopy(surfaces)
