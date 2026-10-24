"""Contract negatives for observation-based assertions, separate from business tests."""

from pathlib import Path
from runpy import run_path

import pytest
from jsonschema import Draft202012Validator

HELPERS = run_path(str(Path(__file__).parents[1] / "graph_testing.py"))


def test_transition_rejects_wrong_object_and_undeclared_result():
    graph = {
        "state_machines": [
            {
                "id": "record",
                "transitions": [{"from": "draft", "event": "submit", "to": "submitted"}],
            }
        ]
    }
    before = {"object_ref": {"object_id": "one"}, "state": "draft"}
    after = {**before, "state": "submitted"}
    check = HELPERS["assert_transition"]
    check(graph, "record", before, "submit", after)
    for value in ({**after, "state": "approved"}, {**after, "object_ref": {"object_id": "two"}}):
        with pytest.raises(AssertionError):
            check(graph, "record", before, "submit", value)


def test_atom_result_checks_real_values_in_both_directions():
    graph = {
        "atoms": [
            {
                "id": "read",
                "kind": "frontend",
                "input_schema": {"type": "string"},
                "output_schema": {"type": "integer"},
            }
        ]
    }
    validate = lambda schema, value: Draft202012Validator(schema).validate(value)
    check = HELPERS["assert_atom_result"]
    check(graph, "read", "key", 2, validate)
    from jsonschema import ValidationError

    for inputs, output in ((2, 2), ("key", "wrong")):
        with pytest.raises(ValidationError):
            check(graph, "read", inputs, output, validate)


def test_backend_atom_resolves_the_matching_manifest_version():
    graph = {
        "atoms": [{"id": "read", "kind": "backend", "capability": {"id": "read", "version": "1"}}]
    }
    manifest = {
        "capabilities": [
            {
                "id": "read",
                "version": "1",
                "input_schema": {"type": "string"},
                "output_schema": {"type": "integer"},
            }
        ]
    }
    check = HELPERS["assert_atom_result"]
    validate = lambda schema, value: Draft202012Validator(schema).validate(value)
    check(graph, "read", "key", 2, validate, manifest=manifest)
    manifest["capabilities"][0]["version"] = "2"
    with pytest.raises(AssertionError):
        check(graph, "read", "key", 2, validate, manifest=manifest)
