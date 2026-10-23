"""Declarative business state graphs; execution and authorization stay in modules.

The workflow navigation cursor is deliberately not a business state. Validation
never imports handlers, evaluates guard text or resolves remote JSON references.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import require
from .registry import Registry


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "contracts/state-machines/v1/definition.schema.json"
)


def state_machine_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_state_machine(definition, flow=None):
    Registry.validate(None, state_machine_schema(), definition)
    states = [state["id"] for state in definition["states"]]
    require(len(states) == len(set(states)), "state_machine_states", "状态编号不能重复。", 422)
    require(
        definition["initial"] in states,
        "state_machine_initial",
        "初始状态必须引用已声明状态。",
        422,
    )
    transitions = definition["transitions"]
    ids = [transition["id"] for transition in transitions]
    require(len(ids) == len(set(ids)), "state_machine_transitions", "状态迁移编号不能重复。", 422)
    routes = [(t["from"], t["step_id"], t["action_id"]) for t in transitions]
    require(
        len(routes) == len(set(routes)),
        "state_machine_ambiguous",
        "同一状态、步骤和动作不能声明多条迁移。",
        422,
    )
    for transition in transitions:
        require(
            transition["from"] in states and transition["to"] in states,
            "state_machine_endpoint",
            "状态迁移的起点和终点必须已声明。",
            422,
        )
        if flow is not None:
            step = next(
                (step for step in flow["steps"] if step["id"] == transition["step_id"]), None
            )
            require(
                step is not None
                and any(action["id"] == transition["action_id"] for action in step["actions"]),
                "state_machine_action",
                "状态迁移必须引用该流程真实声明的步骤和动作。",
                422,
            )
