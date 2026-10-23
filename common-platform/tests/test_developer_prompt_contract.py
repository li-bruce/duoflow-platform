"""Exercise the exported developer contract using synthetic, isolated projects.

These tests never open deployment data, credentials, or environment files. The
only project files copied into temporary roots are the generator's safe sources.
"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from app.module_contract import normalize_surfaces
from app.registry import Registry
from app.remote_services import RemoteServiceManager
from app.workspace_contract import FILE_SCHEMA, REQUEST_SCHEMA, RESPONSE_SCHEMA, SKILL_SCHEMA, WORKSPACE_SCHEMA, normalize_skills, validate_files, validate_workspace
from app.errors import PlatformError
from scripts import build_module_developer_prompt as builder


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {"data", "logs", ".venv", "node_modules", ".npm-cache", ".git", ".test-runtime", "__pycache__"}


def assert_safe_source(relative):
    path = Path(relative)
    assert not path.is_absolute(), "Source inventory must use project-relative paths"
    assert ".." not in path.parts, "Source inventory must remain inside its root"
    assert not EXCLUDED_PARTS.intersection(path.parts), f"Runtime or secret-bearing directory was tracked: {relative}"
    assert not path.name.startswith(".env"), f"Environment file was tracked: {relative}"
    assert path not in {builder.PROMPT_PATH, builder.COMPONENT_PROMPT_PATH, builder.QUICK_PROMPT_PATH, builder.SNAPSHOT_PATH}, "Generated outputs must not fingerprint themselves"


@pytest.fixture
def snapshot():
    return builder.build_snapshot(ROOT)


@pytest.fixture
def isolated_root(tmp_path):
    """Copy only declared source files; never copy a deployment directory."""
    sources = builder.tracked_sources(ROOT)
    assert sources
    for relative in sources:
        assert_safe_source(relative)
    for relative in sources:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    builder.write_outputs(tmp_path, reviewed=True)
    assert builder.check_outputs(tmp_path) == []
    return tmp_path


def test_checked_in_prompt_and_snapshot_are_fresh():
    """The deliverable must be reproducible from the current safe source set."""
    assert builder.check_outputs(ROOT) == []
    outputs = builder.build_outputs(ROOT)
    assert builder.PROMPT_PATH in outputs and builder.SNAPSHOT_PATH in outputs
    stored = json.loads((ROOT / builder.SNAPSHOT_PATH).read_text(encoding="utf-8-sig"))
    assert stored["sources"] == builder.tracked_sources(ROOT)
    assert stored["contract_digest"] == builder.build_snapshot(ROOT)["contract_digest"]


def test_component_prompt_contains_current_sdk_schema_and_both_backends():
    outputs = builder.build_outputs(ROOT)
    component = outputs[builder.COMPONENT_PROMPT_PATH]
    assert builder.build_snapshot(ROOT)['contract_digest'] in component
    for relative in ('web/src/interactions/sdk.ts', 'templates/interaction-module/component/Component.tsx',
                     'templates/interaction-module/backend-python/handlers.py', 'templates/interaction-module/backend-node/server.mjs'):
        assert builder.read(ROOT, relative).strip() in component
    assert 'POST /instances/{id}/actions/{action}' in component


def test_quick_prompt_and_module_kit_export_share_the_reviewed_contract():
    snapshot = builder.build_snapshot(ROOT)
    outputs = builder.build_outputs(ROOT)
    quick = outputs[builder.QUICK_PROMPT_PATH]
    assert snapshot['contract_digest'] in quick
    assert len(quick) < 6000  # The starting prompt remains usable without protocol appendices.
    assert 'scripts/module_kit.py' in quick and '静态检查' in quick
    files = snapshot['module_kit_files']
    assert files and any(path.endswith('Component.tsx') for path in files)
    assert any(path.endswith('.mjs') for path in files)
    assert any(path.endswith('.py') for path in files)
    for name in (builder.PROMPT_PATH, builder.COMPONENT_PROMPT_PATH):
        for relative, body in files.items():
            assert relative in outputs[name]
            if not relative.endswith('.json'):
                assert body.strip() in outputs[name]
    assert 'scripts/module_kit.py' in snapshot['sources']
    assert 'scripts/build_handbooks.py' in snapshot['sources']


def test_exported_source_preserves_nested_markdown_fences():
    body = 'Example:\n```python\nprint("hello")\n```\n'
    exported = builder.block('README.md', body, 'markdown')
    assert '\n````markdown\n' in exported
    assert exported.endswith('\n````\n')
    assert body in exported


def test_component_styles_participate_in_contract_drift_detection(isolated_root):
    stylesheet=isolated_root/'components/packs/style-probe/component.css'
    stylesheet.parent.mkdir(parents=True,exist_ok=True)
    stylesheet.write_text('.probe { color: black; }\n',encoding='utf-8')
    assert stylesheet.relative_to(isolated_root).as_posix() in builder.tracked_sources(isolated_root)
    assert builder.check_outputs(isolated_root)
    builder.write_outputs(isolated_root,reviewed=True)
    assert builder.check_outputs(isolated_root)==[]
    stylesheet.write_text('.probe { color: navy; }\n',encoding='utf-8')
    assert builder.check_outputs(isolated_root)


def test_workflow_schema_sdk_and_pack_sources_are_exported(snapshot):
    component = builder.build_outputs(ROOT)[builder.COMPONENT_PROMPT_PATH]
    assert builder.read(ROOT, 'web/src/workflows/sdk.ts').strip() in component
    for path in ('workflows/v1/definition.schema.json', 'workflows/v1/adapter.schema.json',
                 'files/v1/file-ref.schema.json', 'files/v1/upload.schema.json', 'jobs/v1/job.schema.json',
                 'registration/v1/definition.schema.json', 'registration/v1/offer.schema.json', 'registration/v1/confirmation.schema.json'):
        assert path in snapshot['workflow_contracts']
        Draft202012Validator.check_schema(snapshot['workflow_contracts'][path])
        assert 'contracts/' + path in component
    assert 'flows/installed.json' in snapshot['sources']
    assert '不重放' in component and 'draft_query' in component


def test_access_contracts_samples_and_server_bridge_are_exported_to_both_prompts(snapshot):
    from app.access_control import DEFINITION_SCHEMA, BINDING_SCHEMA, validate_definition
    from app.access_bridge import DECISION_REQUEST_SCHEMA
    assert snapshot['snapshot_version'] == 3
    assert snapshot['access_contracts']['access/v1/definition.schema.json'] == DEFINITION_SCHEMA
    assert snapshot['access_contracts']['access/v1/binding.schema.json'] == BINDING_SCHEMA
    assert snapshot['access_decision_request_schema'] == DECISION_REQUEST_SCHEMA
    example = snapshot['examples']['access']
    validate_definition(example['definition'])
    Draft202012Validator(BINDING_SCHEMA).validate(example['binding'])
    Draft202012Validator(DECISION_REQUEST_SCHEMA).validate(example['decision_request'])
    assert example['definition']['module_id'] == example['binding']['module_id'] == 'example-access'
    assert example['definition']['experiences'] == []  # A read-only bridge is not a workflow demo.
    outputs = builder.build_outputs(ROOT)
    for name in [builder.PROMPT_PATH, builder.COMPONENT_PROMPT_PATH]:
        prompt = outputs[name]
        for path, body in snapshot['remote_access_templates'].items():
            assert path.startswith('templates/remote-access-client/')
            assert body.strip() in prompt
        assert 'contracts/access/v1/definition.schema.json' in prompt
        assert 'X-Platform-Access' in prompt and '/api/v1/business/open' in prompt
        assert 'workitem_sources' in prompt and '元数据' in prompt
        assert '启用模块 → 由身份管理者授权' in prompt


def test_exported_access_facts_match_actual_engine_bridge_and_router_code(snapshot):
    from app.access_control import CasbinDecisionEngine
    from app.access_bridge import MAX_REQUEST_BYTES, MAX_TICKET_BYTES
    facts = snapshot['facts']['access']
    assert facts['contract_version'] == '1.0'
    assert facts['engine'] == CasbinDecisionEngine.name
    assert facts['engine_dependency'] == 'casbin==1.43.0'
    assert facts['decision_request_bytes'] == MAX_REQUEST_BYTES
    assert facts['decision_ticket_bytes'] == MAX_TICKET_BYTES
    assert facts['ticket_default_ttl_seconds'] == 45
    assert {'method': 'POST', 'path': '/api/v1/access/remote-decisions'} in facts['remote_decision_routes']
    assert facts['business_routes'] == [
        {'method': 'GET', 'path': '/api/v1/business/catalog'},
        {'method': 'GET', 'path': '/api/v1/business/instances'},
        {'method': 'GET', 'path': '/api/v1/business/objects'},
        {'method': 'POST', 'path': '/api/v1/business/open'},
    ]


def test_access_template_export_never_reads_credentials_or_runtime_data(isolated_root):
    template = isolated_root / 'templates/remote-access-client'
    (template / '.env').write_text('SYNTHETIC_PRIVATE_KEY=do-not-export', encoding='utf-8')
    (template / 'data').mkdir()
    (template / 'data/credentials.json').write_text('{"key":"do-not-export"}', encoding='utf-8')
    snapshot = builder.build_snapshot(isolated_root)
    assert 'do-not-export' not in json.dumps(snapshot)
    assert all(not path.endswith('.env') and '/data/' not in path for path in snapshot['remote_access_templates'])
    assert builder.check_outputs(isolated_root) == []


def test_remote_capability_count_accepts_64_and_rejects_65(snapshot):
    base = deepcopy(snapshot['examples']['remote']['manifest'])
    cap = deepcopy(base['capabilities'][0])
    cap['input_schema'] = {'type': 'object'}
    cap['output_schema'] = {'type': 'object'}
    cap.pop('result', None)
    base['capabilities'] = [{**deepcopy(cap), 'id': 'example.capacity.cap_' + str(index)} for index in range(64)]
    identifiers = [item['id'] for item in base['capabilities']]
    base['surfaces'] = {'page_tools': [{'id': 'capacity', 'title': '容量边界', 'description': '', 'kind': 'form', 'capability_ids': identifiers}],
                        'conversation': {'capability_ids': identifiers, 'starter_prompts': []}}
    RemoteServiceManager._validate_manifest(base)
    base['capabilities'].append({**deepcopy(cap), 'id': 'example.capacity.cap_64'})
    base['surfaces']['page_tools'][0]['capability_ids'].append('example.capacity.cap_64')
    from app.remote_services import RemoteServiceError
    with pytest.raises(RemoteServiceError):
        RemoteServiceManager._validate_manifest(base)


def test_exported_remote_contract_passes_discovery_and_invocation_validation(snapshot):
    example = snapshot["examples"]["remote"]
    manifest = deepcopy(example["manifest"])
    accepted = RemoteServiceManager._validate_manifest(manifest)
    request = example["request"]
    response = example["response"]
    Draft202012Validator.check_schema(example["request_schema"])
    Draft202012Validator(example["request_schema"]).validate(request)
    cap = next(item for item in accepted["capabilities"] if item["id"] == request["capability_id"])
    Draft202012Validator(cap["input_schema"]).validate(request["input"])
    Draft202012Validator(cap["output_schema"]).validate(response["data"])
    assert request["capability_version"] == cap["version"]
    assert request["context"]["actor"]["role"] in cap["roles"]
    for field in ("protocol_version", "request_id", "capability_id", "capability_version"):
        assert response[field] == request[field]
    # Discovery and invocation remain explicitly versioned apart.
    assert manifest["protocol_version"] == "1.2"
    assert request["protocol_version"] == "1.1"
    Draft202012Validator(RESPONSE_SCHEMA).validate(response)
    assert request['context']['actor']['role'] in request['context']['actor']['roles']
    assert set(request['context']['actor']['roles']) & set(cap['roles'])
    validate_files(cap['input_schema'], request['input'])
    validate_workspace(response['data'])
    normalize_skills(manifest['surfaces']['conversation']['skills'], accepted['capabilities'])
    assert example["health"]["status"] == "ok"


def test_exported_embedded_tool_can_be_added_without_breaking_dual_surface_coverage(snapshot):
    example = snapshot["examples"]["remote"]
    manifest = deepcopy(example["manifest"])
    tool = deepcopy(example["embedded_tool"])
    manifest["surfaces"]["page_tools"].append(tool)
    normalized = normalize_surfaces(
        manifest["module"]["id"], manifest["capabilities"], manifest["surfaces"], allow_embedded=True, allow_skills=True,
    )
    assert any(item["id"] == tool["id"] and item["kind"] == "embedded" for item in normalized["page_tools"])
    assert set(normalized["conversation"]["capability_ids"]) == {cap["id"] for cap in manifest["capabilities"]}
    RemoteServiceManager._validate_manifest(manifest)


def test_exported_local_files_install_and_execute_the_echo_handler(snapshot, tmp_path):
    example = snapshot["examples"]["local"]
    manifest = deepcopy(example["manifest"])
    extensions = tmp_path / "extensions"
    package = extensions / "exported-example"
    package.mkdir(parents=True)
    (extensions / "installed.json").write_text(json.dumps({"modules": [package.name]}), encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for capability in manifest["capabilities"]:
        for direction in ("input", "output"):
            path = package / capability[f"{direction}_schema"]
            assert path.resolve().is_relative_to(package.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(example["schemas"][direction], ensure_ascii=False), encoding="utf-8")
        filename = capability["handler"].split(":", 1)[0] + ".py"
        assert Path(filename).name == filename
        (package / filename).write_text(example["handler"], encoding="utf-8")
    registry = Registry(tmp_path)
    registry.load()
    actor = {'role': 'researcher', 'roles': ['researcher', 'member']}
    capability = registry.get(manifest["capabilities"][0]["id"], actor)
    message = "  合成开发契约验收\nPreserve whitespace, 中文与换行。  "
    payload = {"message": message}
    registry.validate(capability["input_schema"], payload)
    result = asyncio.run(registry.handlers[capability["id"]](SimpleNamespace(), payload))
    registry.validate(capability["output_schema"], result.data)
    assert result.data == {"echo": message}
    assert any(view["type"] == "text-result" and view["data"]["text"] == message for view in result.views)
    public = registry.public(actor)
    surfaces = public["modules"][0]["surfaces"]
    assert capability["id"] in surfaces["conversation"]["capability_ids"]
    assert any(capability["id"] in tool["capability_ids"] for tool in surfaces["page_tools"])
    assert surfaces['conversation']['skills'][0]['entry_capability'] == capability['id']


def test_portable_public_schemas_match_enforcing_code_and_reject_unsafe_content(snapshot):
    expected = {'invoke-request-1.1.schema.json': REQUEST_SCHEMA, 'invoke-response-1.1.schema.json': RESPONSE_SCHEMA,
                'workspace.schema.json': WORKSPACE_SCHEMA, 'platform-file.schema.json': FILE_SCHEMA, 'skill.schema.json': SKILL_SCHEMA}
    for filename, schema in expected.items():
        exported = snapshot['contracts'][filename]
        Draft202012Validator.check_schema(exported)
        assert {key: value for key, value in exported.items() if key != '$schema'} == schema
        assert 'contracts/v1.2/' + filename in snapshot['sources']
    assert 'config/identity-policy.json' in snapshot['sources']
    assert not snapshot['facts']['identity_policy']['roles']['system_admin']['implies']
    injected = deepcopy(snapshot['examples']['remote']['request'])
    injected['context']['actor']['permissions'] = ['resources.read_all']
    assert not Draft202012Validator(snapshot['contracts']['invoke-request-1.1.schema.json']).is_valid(injected)
    data = deepcopy(snapshot['examples']['remote']['response']['data'])
    data['blocks'].append({'type': 'html', 'html': '<script>alert(1)</script>'})
    with pytest.raises(PlatformError):
        validate_workspace(data)
    invalid_file = {'filename': '../private.txt', 'media_type': 'text/plain', 'content_base64': 'YQ=='}
    with pytest.raises(PlatformError):
        validate_files(snapshot['contracts']['platform-file.schema.json'], invalid_file)


def test_legacy_reference_remains_separate_and_valid(snapshot):
    legacy = snapshot['examples']['legacy_remote']
    RemoteServiceManager._validate_manifest(legacy['manifest'])
    Draft202012Validator(legacy['request_schema']).validate(legacy['request'])
    assert legacy['manifest']['protocol_version'] == '1.1'
    assert legacy['request']['protocol_version'] == '1.0'
    assert 'roles' not in legacy['request']['context']['actor']
    assert 'workspace' not in snapshot['facts']['legacy_remote_renderers']
    assert 'workspace' in snapshot['facts']['remote_renderers']


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_saved_contract_detects_source_edits_additions_and_deletions(isolated_root, change):
    root = isolated_root
    extra = root / "app" / "prompt_contract_added.py"
    if change == "delete":
        extra.write_text('"""Synthetic contract source used only by this test."""\n', encoding="utf-8")
        assert extra.relative_to(root).as_posix() in builder.tracked_sources(root)
        builder.write_outputs(root, reviewed=True)
        assert builder.check_outputs(root) == []
    old_digest = builder.build_snapshot(root)["contract_digest"]
    old_outputs = {path: (root / path).read_bytes() for path in builder.build_outputs(root)}
    if change == "edit":
        target = root / "app" / "registry.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# Synthetic source revision for drift detection.\n", encoding="utf-8")
    elif change == "add":
        extra.write_text('"""Synthetic newly added source."""\n', encoding="utf-8")
    else:
        extra.unlink()
    assert builder.check_outputs(root), f"The persisted export did not detect a source {change}"
    assert builder.build_snapshot(root)["contract_digest"] != old_digest
    # Checking reports drift; it must not silently approve or rewrite artifacts.
    assert {path: (root / path).read_bytes() for path in old_outputs} == old_outputs


def test_runtime_data_and_environment_are_excluded_even_when_they_look_like_source(isolated_root, monkeypatch):
    root = isolated_root
    sentinel = "SYNTHETIC_PRIVATE_VALUE_FOR_PROMPT_CONTRACT_TEST_ONLY"
    baseline = builder.build_outputs(root)
    locations = [
        "data/private.py", "logs/private.json", ".env", "config/.env",
        "examples/remote-node-service/data/private.cjs", "examples/remote-text-service/data/private.py",
        "templates/remote-service-template/data/private.json", "web/node_modules/private/index.ts",
        "web/.npm-cache/private.json", ".git/private.py",
    ]
    for relative in locations:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sentinel, encoding="utf-8")
    monkeypatch.setenv("PROMPT_CONTRACT_TEST_SECRET", sentinel)
    private_paths = {(root / relative).resolve() for relative in locations}
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.resolve() not in private_paths, "The generator attempted to open excluded runtime data"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    sources = builder.tracked_sources(root)
    for relative in sources:
        assert_safe_source(relative)
    assert not set(locations).intersection(sources)
    assert builder.build_outputs(root) == baseline
    assert builder.check_outputs(root) == []
    assert sentinel not in json.dumps(builder.build_snapshot(root), ensure_ascii=False)


def test_outputs_cannot_be_rebuilt_without_explicit_review(isolated_root):
    root = isolated_root
    before = {path: (root / path).read_bytes() for path in builder.build_outputs(root)}
    with pytest.raises(ValueError):
        builder.write_outputs(root, reviewed=False)
    assert {path: (root / path).read_bytes() for path in before} == before
