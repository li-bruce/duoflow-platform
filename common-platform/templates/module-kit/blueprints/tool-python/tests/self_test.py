"""Offline business/SDK checks; no account, database, server or dependency needed.

Run from any working directory: python <generated-package>/tests/self_test.py
The decision fake checks the SDK boundary, not the platform's policy engine.
The framework integration suite separately verifies real grants and revocation.
"""
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = run_path(str(ROOT / "backend" / "_module_kit.py"))
BUSINESS = run_path(str(ROOT / "backend" / "business.py"))
MANIFEST = json.loads((ROOT / "backend" / "manifest.json").read_text(encoding="utf-8"))
PERMISSION = MANIFEST["id"] + ".use"


class DecisionFake:
    def __init__(self, allowed=True, organization_id="org-a"):
        self.allowed, self.organization_id, self.calls = allowed, organization_id, []

    def check(self, actor, permission, facts):
        self.calls.append((actor.copy(), permission, facts.copy()))
        return {"allowed": self.allowed if facts == {"organization_id": self.organization_id} else False}


def context(access=None, **actor_fields):
    return SimpleNamespace(actor={"id": "synthetic-user", "organization_id": "org-a", "roles": ["member"], **actor_fields},
                           access=DecisionFake() if access is None else access)


def invoke(ctx, payload=None, handler=None):
    return SDK["run_tool"](ctx, permission=PERMISSION, payload={"text": "example"} if payload is None else payload,
                           handler=BUSINESS["analyze"] if handler is None else handler)


class ToolTests(unittest.TestCase):
    def assert_error(self, code, thunk):
        with self.assertRaises(SDK["KitError"]) as caught:
            thunk()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_normalizes_line_endings_and_reports_exact_counts(self):
        result = invoke(context(), {"text": " 甲\t乙 \r\n\r\n hello   world \r final "})
        self.assertEqual(result["data"], {"normalized_text": "甲 乙\n\nhello world\nfinal", "character_count": 22,
                                          "line_count": 4, "segment_count": 5})
        self.assertIn("22", result["summary"])

    def test_multilingual_count_is_codepoints_not_bytes(self):
        data = invoke(context(), {"text": "中文 🙂"})["data"]
        self.assertEqual(data["character_count"], 4)
        self.assertEqual(data["segment_count"], 2)

    def test_business_is_pure_and_input_is_unchanged(self):
        payload = {"text": " a  b "}
        result = BUSINESS["analyze"](payload)
        self.assertEqual(payload, {"text": " a  b "})
        self.assertEqual(result, BUSINESS["analyze"](payload))

    def test_maximum_input_and_output_fit_declared_schema(self):
        data = invoke(context(), {"text": "字" * 20000})["data"]
        self.assertEqual(data["character_count"], 20000)
        schema = MANIFEST["capabilities"][0]["output_schema"]
        self.assertEqual(set(data), set(schema["required"]))
        for name, value in data.items():
            field = schema["properties"][name]
            self.assertTrue(type(value) is int if field["type"] == "integer" else isinstance(value, str))
            self.assertLessEqual(value if type(value) is int else len(value), field.get("maximum", field.get("maxLength")))

    def test_invalid_inputs_do_not_pass_business_boundary(self):
        for payload in ({}, {"text": ""}, {"text": " \n "}, {"text": None}, {"text": 42}, {"text": "x" * 20001}, {"text": "x", "organization_id": "org-b"}):
            with self.subTest(payload_type=str(type(payload.get("text")))):
                self.assert_error("tool_input_invalid", lambda: invoke(context(), payload))

    def test_scope_facts_come_only_from_authenticated_actor(self):
        ctx = context()
        invoke(ctx)
        self.assertEqual(ctx.access.calls[0][1:], (PERMISSION, {"organization_id": "org-a"}))

    def test_other_organization_is_denied_before_business_runs(self):
        calls = []
        self.assert_error("tool_forbidden", lambda: invoke(context(DecisionFake(organization_id="org-b")), handler=lambda value: calls.append(value)))
        self.assertEqual(calls, [])

    def test_no_global_admin_or_legacy_role_fallback(self):
        self.assert_error("tool_forbidden", lambda: invoke(context(DecisionFake(False), roles=["system_admin", "admin", "member"])))

    def test_missing_identity_or_organization_fails_closed(self):
        for fields in ({"id": ""}, {"enabled": False}, {"organization_id": None}, {"organization_id": " "}, {"organization_id": "*"}):
            ctx = context(**fields)
            with self.subTest(fields=fields):
                with self.assertRaises(SDK["KitError"]):
                    invoke(ctx)
                self.assertEqual(ctx.access.calls, [])

    def test_missing_or_failed_access_service_fails_closed(self):
        self.assert_error("tool_access_unavailable", lambda: invoke(SimpleNamespace(actor=context().actor, access=None)))

        class Unavailable:
            def check(self, *args):
                raise RuntimeError("private service detail")

        error = self.assert_error("tool_access_unavailable", lambda: invoke(context(Unavailable())))
        self.assertNotIn("private", error.message)

    def test_malformed_decision_cannot_be_truthy_allow(self):
        for value in (1, "true", None, [], {}):
            self.assert_error("tool_forbidden", lambda: invoke(context(DecisionFake(value))))

    def test_json_input_and_size_guards(self):
        for payload in ([], {"text": float("nan")}, {"text": "\ud800"}):
            self.assert_error("tool_input_invalid", lambda: invoke(context(), payload))
        self.assert_error("tool_input_too_large", lambda: invoke(context(), {"text": "a" * 100001}))

    def test_result_contract_and_json_guards(self):
        for result in (None, {}, {"summary": "", "data": {}}, {"summary": "ok", "data": []},
                       {"summary": "ok", "data": {}, "token": "not permitted"},
                       {"summary": "ok", "data": {"number": float("inf")}}):
            self.assert_error("tool_result_invalid", lambda: invoke(context(), handler=lambda payload: result))
        self.assert_error("tool_result_too_large", lambda: invoke(context(), handler=lambda payload: {"summary": "ok", "data": {"text": "x" * 256001}}))

    def test_grant_contract_matches_installed_capability(self):
        definition = json.loads((ROOT / "backend" / "access-definition.json").read_text(encoding="utf-8"))
        self.assertEqual(definition["module_id"], MANIFEST["id"])
        self.assertEqual(definition["command_mappings"], [{"capability_id": MANIFEST["id"] + ".analyze", "permission": PERMISSION}])
        self.assertEqual(definition["role_templates"][0]["required_scope"], ["organization_id"])
        self.assertEqual(definition["experiences"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
