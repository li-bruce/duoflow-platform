"""Generated packages against real framework validators, Runtime and AccessBridge.

No app.main import, installed services, production accounts or external business.
The caller supplies an isolated --basetemp under data/coordinator-checks/<id>.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
import json
import re
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.access_bridge import create_access_bridge_router
from app.business_center import BusinessCenter
from app.conversation import ConversationService
from app.db import IdentityProfile, Invocation, LoginSession, Organization, User, create_database, user_dict
from app.errors import PlatformError
from app.interaction_routes import create_interaction_router
from app.remote_services import RemoteServiceManager
from app.runtime import Runtime
from app.security import COOKIE, check_csrf, require_user, token_hash

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('module_kit_cli', ROOT / 'scripts/module_kit.py')
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)
NODE = Path(os.environ.get('NODE_EXE') or shutil.which('node') or 'node')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generated(tmp_path, template='tool-python', name='kit.text'):
    output = tmp_path / ('n' if template == 'workflow-node' else 'p')
    kit.scaffold(template, name, '合成模块', output)
    return output


def mutate(root, path, callback):
    value = json.loads((root / path).read_text(encoding='utf-8'))
    callback(value)
    write_json(root / path, value)


@pytest.mark.parametrize('template,module_id', [('tool-python', 'kit.text'), ('workflow-node', 'kit.review')])
def test_scaffold_creates_valid_uninstalled_package(tmp_path, template, module_id):
    package = generated(tmp_path, template, module_id)
    result = kit.check(package)
    assert result['status'] == 'valid' and result['module_id'] == module_id
    assert result['installed'] is False and result['executed_supplier_code'] is False
    assert not list(package.rglob('installed.json'))
    assert not list(package.rglob('*.sqlite'))
    meta = json.loads((package / 'module-kit.json').read_text(encoding='utf-8'))
    assert meta['pack_name'] == module_id.replace('.', '-')
    assert not list(package.rglob('__pycache__'))


def test_scaffold_refuses_overwrite_and_leaves_existing_bytes(tmp_path):
    package = generated(tmp_path)
    sentinel = package / 'personal.txt'; sentinel.write_bytes(b'keep')
    with pytest.raises(kit.KitError, match='已经存在'):
        kit.scaffold('tool-python', 'other.tool', '工具', package)
    assert sentinel.read_bytes() == b'keep'


@pytest.mark.parametrize('case', ['unknown-action', 'duplicate-edge', 'unknown-state'])
def test_workflow_machine_references_are_checked_without_running_code(tmp_path, case):
    package = generated(tmp_path, 'workflow-node', 'kit.machine')
    document = json.loads((package / 'frontend/flow.json').read_text(encoding='utf-8'))
    machine = document['flows'][0]['state_machine']
    if case == 'unknown-action': machine['transitions'][0]['action_id'] = 'unregistered'
    elif case == 'duplicate-edge': machine['transitions'].append({**machine['transitions'][0], 'id': 'other-edge'})
    else: machine['transitions'][0]['to'] = 'missing-state'
    write_json(package / 'frontend/flow.json', document)
    with pytest.raises(PlatformError): kit.check(package)


@pytest.mark.parametrize('module_id', ['core', 'core.use', '../escape', 'Bad.Name', 'a..b', 'con', 'x' * 61])
def test_scaffold_rejects_unsafe_namespace(tmp_path, module_id):
    with pytest.raises(kit.KitError):
        kit.scaffold('tool-python', module_id, '工具', tmp_path / 'new')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('title', ['"; import os', '</script>', 'hello\nworld', '__TITLE__', '`code`'])
def test_scaffold_title_cannot_inject_code(tmp_path, title):
    with pytest.raises(kit.KitError):
        kit.scaffold('tool-python', 'kit.text', title, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


def test_check_never_imports_supplier_handler(tmp_path):
    package = generated(tmp_path)
    marker = tmp_path / 'executed'
    handler = package / 'backend/handlers.py'
    handler.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\ndef analyze(ctx,payload): pass\n', encoding='utf-8')
    assert kit.check(package)['executed_supplier_code'] is False
    assert not marker.exists()


@pytest.mark.parametrize('case', ['missing_sdk', 'sdk_drift', 'foreign_permission', 'missing_mapping',
                                 'wrong_version', 'unknown_state_binding', 'duplicate_json'])
def test_check_rejects_broken_tool_package(tmp_path, case):
    package = generated(tmp_path)
    if case == 'missing_sdk':
        (package / 'backend/_module_kit.py').unlink()
    elif case == 'sdk_drift':
        (package / 'backend/_module_kit.py').write_text('# changed', encoding='utf-8')
    elif case == 'foreign_permission':
        mutate(package, 'backend/access-definition.json', lambda v: v['permissions'][0].update(id='other.use'))
    elif case == 'missing_mapping':
        mutate(package, 'backend/access-definition.json', lambda v: v.update(command_mappings=[]))
    elif case == 'wrong_version':
        mutate(package, 'frontend/interaction.json', lambda v: v['actions'][0].update(capability_version='9.0.0'))
    elif case == 'unknown_state_binding':
        mutate(package, 'frontend/interaction.json', lambda v: next(iter(v['actions'][0]['bindings'].values())).update(key='foreign'))
    else:
        path = package / 'module-kit.json'
        path.write_text(path.read_text(encoding='utf-8').replace('"kit_version":', '"kit_version":"different","kit_version":'), encoding='utf-8')
    with pytest.raises(Exception):
        kit.check(package)


@pytest.mark.parametrize('case', ['provider', 'action', 'experience', 'step', 'target', 'schema', 'module_id', 'group', 'component'])
def test_check_rejects_broken_workflow_references(tmp_path, case):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    if case == 'module_id':
        mutate(package, 'backend/access-definition.json', lambda v: v.update(module_id='foreign.module'))
    elif case in {'experience', 'step'}:
        mutate(package, 'backend/access-definition.json', lambda v: v['experiences'][0].update(
            **({'flow_id': 'kit.review.missing'} if case == 'experience' else {'step_ids': ['missing']})))
    else:
        def damage(value):
            flow = value['flows'][0]
            if case == 'provider': flow['provider']['read_capability'] = 'foreign.read'
            elif case == 'action': flow['steps'][0]['actions'][0]['capability_id'] = 'foreign.create'
            elif case == 'target': flow['steps'][0]['actions'][0]['target_step'] = 'missing'
            elif case == 'schema': flow['steps'][0]['actions'][0]['command_schema'] = {'type': 'object'}
            elif case == 'group': flow['page_groups'][0]['steps'] = ['missing']
            elif case == 'component': flow['steps'][0]['component'] = 'AbsentWidget'
        mutate(package, 'frontend/flow.json', damage)
    with pytest.raises(Exception):
        kit.check(package)


def test_existing_python_remote_adaptation_is_checked_without_rewriting_service(tmp_path):
    package = generated(tmp_path, 'workflow-node', 'intake-kit')
    mutate(package, 'module-kit.json', lambda v: v.update(template='workflow-python-remote', runtime='python-remote'))
    for name in ('server.py', 'workflow.py'):
        (package / 'backend' / name).write_text('raise RuntimeError("check must never execute adapted service")\n', encoding='utf-8')
    (package / 'sdk/python').mkdir()
    shutil.copyfile(ROOT / 'templates/module-kit/sdk/python/remote_access.py', package / 'sdk/python/remote_access.py')
    shutil.copyfile(ROOT / 'templates/module-kit/sdk/python/registration.py', package / 'sdk/python/registration.py')
    (package / 'tests/test_adapter.py').write_text('# Adapter integration tests are supplied by the existing service.\n', encoding='utf-8')
    result = kit.check(package)
    assert result['template'] == 'workflow-python-remote'
    assert 'RuntimeError' in (package / 'backend/server.py').read_text(encoding='utf-8')


def test_python_package_runs_its_independent_business_tests(tmp_path):
    package = generated(tmp_path)
    result = subprocess.run([sys.executable, str(package / 'tests/self_test.py')], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Ran 14 tests' in result.stderr


@pytest.mark.parametrize('replacement', ['{Picker, Editor}', '{Picker, Editor, ...{Review}}', '{Picker, Editor, ["Review"]:Review}'])
def test_check_requires_actual_static_step_export_not_a_word_in_source(tmp_path, replacement):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    path = package / 'frontend/steps.tsx'
    content = path.read_text(encoding='utf-8')
    assert 'function Review' in content
    changed, count = re.subn(r'(export\s+const\s+stepComponents\s*(?::[^=]+)?=\s*)\{[^}]*\}',
                             lambda match: match.group(1) + replacement, content, count=1)
    assert count == 1, 'The corruption scenario must modify the actual component map.'
    path.write_text(changed, encoding='utf-8')
    with pytest.raises(kit.KitError):
        kit.check(package)


def test_check_rejects_undeclared_default_component(tmp_path):
    package = generated(tmp_path)
    (package / 'frontend/Component.tsx').write_text("import type {InteractionProps} from '@platform/interaction-sdk';\nexport default Missing;\n", encoding='utf-8')
    with pytest.raises(kit.KitError):
        kit.check(package)


def test_check_rejects_type_only_import_as_step_component(tmp_path):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    (package / 'frontend/steps.tsx').write_text("import type {WorkflowStepProps} from '@platform/workflow-sdk';\nexport const stepComponents={Picker:WorkflowStepProps,Editor:WorkflowStepProps,Review:WorkflowStepProps};\n", encoding='utf-8')
    with pytest.raises(kit.KitError):
        kit.check(package)


def test_workflow_checks_optional_interaction_attachment_and_bindings(tmp_path):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    manifest = kit.read_json(package, 'backend/manifest.json')
    command = next(cap for cap in manifest['capabilities'] if cap['id'] == 'kit.review.create')['input_schema']['properties']['command']
    attachment = {'protocol_version': '1.0', 'id': 'kit.review.quick-create', 'version': '1.0.0',
                  'title': '快速创建', 'description': '同一能力的即时组件', 'triggers': ['快速创建'],
                  'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                  'state_schema': {'type': 'object', 'properties': {'command': command}, 'required': ['command'], 'additionalProperties': False},
                  'initial_state': {'command': {'project_id': 'P', 'title': '参考', 'body': ''}},
                  'actions': [{'id': 'create', 'title': '创建', 'capability_id': 'kit.review.create', 'capability_version': '1.0.0',
                               'completes': True, 'bindings': {'command': {'source': 'state', 'key': 'command'}}}]}
    write_json(package / 'frontend/interaction.json', attachment)
    (package / 'frontend/Component.tsx').write_text("import type {InteractionProps} from '@platform/interaction-sdk';\nexport default function Quick(p:InteractionProps){return <p>{p.surface}</p>}\n", encoding='utf-8')
    assert kit.check(package)['frontend_ids'] == ['kit.review.workflow', 'kit.review.quick-create']
    attachment['actions'][0]['bindings']['command']['key'] = 'missing'
    write_json(package / 'frontend/interaction.json', attachment)
    with pytest.raises(kit.KitError, match='绑定'):
        kit.check(package)


def test_node_package_runs_real_http_and_persistence_tests(tmp_path):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    assert NODE.is_file(), 'Configure NODE_EXE to bundled Node 24+'
    result = subprocess.run([str(NODE), '--test', str(package / 'tests/workflow.test.mjs')], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stdout + result.stderr


def platform_root(path, package=None, kind='tool'):
    (path / 'extensions').mkdir(parents=True)
    (path / 'extensions/installed.json').write_text('{"modules":[]}', encoding='utf-8')
    shutil.copytree(ROOT / 'config', path / 'config')
    if package:
        meta = kit.read_json(package, 'module-kit.json'); pack = meta['pack_name']
        if kind == 'tool':
            shutil.copytree(package / 'backend', path / 'extensions' / pack)
            write_json(path / 'extensions/installed.json', {'modules': [pack]})
        prefix = 'components' if kind == 'tool' else 'flows'
        shutil.copytree(package / 'frontend', path / prefix / 'packs' / pack)
        write_json(path / prefix / 'installed.json', {'packs': [pack]})
    engine, sessions = create_database(path / 'data')
    actors = {}
    with sessions.begin() as db:
        db.add_all([Organization(id=value, name=value) for value in ('org-a', 'org-b')]); db.flush()
        for name, org, role in [('owner', 'org-a', 'member'), ('reviewer', 'org-a', 'member'),
                                ('outside', 'org-b', 'member'), ('manager', 'org-a', 'system_admin')]:
            user = User(id=name, username=name, name=name, role=role, password_hash='unused')
            db.add(user); db.flush()
            db.add(IdentityProfile(user_id=name, roles=[role], organization_id=org, authorization_revision=1))
        db.flush()
        actors = {name: user_dict(db.get(User, name)) for name in ('owner', 'reviewer', 'outside', 'manager')}
    return engine, sessions, actors


def test_installed_python_tool_uses_real_grants_csrf_and_private_restoration(tmp_path):
    package = generated(tmp_path)
    base = tmp_path / 'host'
    engine, sessions, actors = platform_root(base, package)
    gateway = SimpleNamespace(configured=False, redact=lambda v: v)
    runtime = Runtime(base, sessions, gateway, data_dir=base / 'data')
    assert runtime.access.module_registered('kit.text')
    for actor in actors.values():
        with pytest.raises(PlatformError):
            asyncio.run(runtime.invoke(actor, 'kit.text.analyze', {'text': 'private'}, 'no-grant'))
    wrong = runtime.access.grant('outside', 'kit.text', 'kit.text.user', {'organization_id': 'org-a'}, actor_id='manager')
    with pytest.raises(PlatformError):
        asyncio.run(runtime.invoke(actors['outside'], 'kit.text.analyze', {'text': 'private'}, 'wrong-org'))
    grant = runtime.access.grant('owner', 'kit.text', 'kit.text.user', {'organization_id': 'org-a'}, actor_id='manager')
    app = FastAPI(); app.state.sessions = sessions

    @app.exception_handler(PlatformError)
    async def error(request, exc):
        return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status)

    app.include_router(create_interaction_router(runtime.interactions, require_user, check_csrf))
    cookie, csrf = secrets.token_hex(24), secrets.token_hex(24)
    with sessions.begin() as db:
        db.add(LoginSession(token_hash=token_hash(cookie), user_id='owner', csrf=csrf, expires=time.time() + 600))
    try:
        with TestClient(app) as client:
            client.cookies.set(COOKIE, cookie)
            body = {'component_id': 'kit.text.tool', 'input': {}, 'idempotency_key': 'open-tool'}
            assert client.post('/api/v1/interactions/instances', json=body).status_code == 403
            client.headers['X-CSRF-Token'] = csrf
            opened = client.post('/api/v1/interactions/instances', json=body)
            assert opened.status_code == 200, opened.text
            item = opened.json(); instance_id = item['id']
            changed = client.patch('/api/v1/interactions/instances/' + instance_id,
                                   json={'state': {'text': ' 甲 乙\nHello '}, 'expected_revision': item['revision']})
            assert changed.status_code == 200, changed.text
            item = changed.json()
            action_id = kit.read_json(package, 'frontend/interaction.json')['actions'][0]['id']
            receipt = client.post(f'/api/v1/interactions/instances/{instance_id}/actions/{action_id}',
                                  json={'expected_revision': item['revision'], 'idempotency_key': 'analyze-once'})
            assert receipt.status_code == 200, receipt.text
            current = client.get('/api/v1/interactions/instances/' + instance_id).json()
            assert current['result']['data']['normalized_text'] == '甲 乙\nHello'
            attached = runtime.interactions.attach(actors['owner'], instance_id)
            conversations = ConversationService(runtime)
            before = runtime.interactions.get(actors['owner'], instance_id)
            fork = conversations.fork(actors['owner'], attached['conversation_id'])
            assert fork['id'] != attached['conversation_id']
            with sessions() as db:
                assert db.query(Invocation).filter_by(capability_id='kit.text.analyze', status='completed').count() == 1
            restart = Runtime(base, sessions, gateway, data_dir=base / 'data')
            assert restart.interactions.get(actors['owner'], instance_id)['result'] == before['result']
            runtime.access.revoke(grant['id'], actor_id='manager')
            assert client.get('/api/v1/interactions/instances/' + instance_id).status_code == 403
    finally:
        engine.dispose()


@contextmanager
def bridge_http(runtime, sessions):
    app = FastAPI(); app.state.sessions = sessions
    app.include_router(create_access_bridge_router(runtime.access_bridge))

    @app.exception_handler(PlatformError)
    async def error(request, exc):
        return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status)

    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        yield url
    finally:
        server.should_exit = True; thread.join(timeout=10); sock.close()
        assert not thread.is_alive()


def start_node(package, data, callback, token):
    env = {**os.environ, 'PORT': '0', 'SERVICE_DATA_DIR': str(data), 'PLATFORM_SERVICE_TOKEN': token,
           'PLATFORM_ACCESS_DECISION_URL': callback + '/api/v1/access/remote-decisions',
           'MODULE_PROJECTS_FILE': str(package / 'backend/projects.example.json')}
    process = subprocess.Popen([str(NODE), str(package / 'backend/server.mjs')], env=env, cwd=package,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=10)
        assert line, process.stderr.read()
        ready = json.loads(line); assert ready['ready'] is True
        return process, f"http://127.0.0.1:{ready['port']}"
    except Exception:
        process.terminate(); process.wait(timeout=5); raise


def test_generated_node_roundtrip_real_central_grants_business_center_and_branches(tmp_path):
    package = generated(tmp_path, 'workflow-node', 'kit.review')
    write_json(package / 'backend/projects.example.json', [
        {'project_id': 'P', 'organization_id': 'org-a', 'title': '项目A'},
        {'project_id': 'Q', 'organization_id': 'org-b', 'title': '项目B'},
    ])
    base = tmp_path / 'host'
    engine, sessions, actors = platform_root(base, package, 'workflow')
    manager = RemoteServiceManager(base / 'data/remote')
    runtime = Runtime(base, sessions, SimpleNamespace(configured=False, redact=lambda v: v), manager, base / 'data')
    token = secrets.token_urlsafe(32); process = None
    try:
        with bridge_http(runtime, sessions) as callback:
            process, url = start_node(package, tmp_path / 'business', callback, token)
            assert httpx.get(url + '/platform/v1/manifest').status_code == 401
            manager.save_connection({'id': 'kit-reference', 'name': '合成参考包', 'base_url': url, 'token': token, 'timeout_seconds': 10})
            discovered = asyncio.run(manager.discover('kit-reference'))
            # Discovery remains disabled; frontend is already in the isolated tree.
            rejected = asyncio.run(manager.admit('kit-reference', discovered['revision'],
                host_check=lambda document: runtime.check_remote_registration('kit-reference', document)))
            assert not rejected['admission']['passed']
            assert rejected['admission']['checks'][-1]['id'] == 'host'
            runtime.access.register_definition(kit.read_json(package, 'backend/access-definition.json'), actor_id='manager')
            accepted = asyncio.run(manager.admit('kit-reference', discovered['revision'],
                host_check=lambda document: runtime.check_remote_registration('kit-reference', document)))
            assert accepted['admission']['passed'], accepted['admission']
            runtime.set_remote_publication('kit-reference', True, discovered['revision'])
            for actor in actors.values():
                with pytest.raises(PlatformError):
                    asyncio.run(runtime.workflows.create(actor, 'kit.review.workflow', 'without-grant'))
            grants = {}
            for name, role, project, org in [('owner', 'author', 'P', 'org-a'), ('reviewer', 'reviewer', 'P', 'org-a'), ('outside', 'author', 'Q', 'org-b')]:
                grants[name] = runtime.access.grant(name, 'kit.review', 'kit.review.' + role,
                    {'project_id': project, 'organization_id': org}, actor_id='manager')
            owner, reviewer = actors['owner'], actors['reviewer']
            flow = asyncio.run(runtime.workflows.create(owner, 'kit.review.workflow', 'open-flow'))

            def invoke(actor, action, payload, key):
                result = asyncio.run(runtime.invoke(actor, 'kit.review.' + action, payload, key, origin='conversation'))
                asyncio.run(runtime.process_run(result['run_id']))
                with sessions() as db:
                    from app.db import Run
                    run = db.get(Run, result['run_id'])
                    assert run.status == 'succeeded', run.error
                    return run.result

            response = asyncio.run(runtime.workflows.action(owner, flow['id'], 'create',
                {'project_id': 'P', 'title': '合成记录', 'body': '初稿'}, flow['revision'], None, 'create-reference'))
            asyncio.run(runtime.process_run(response['instance']['run_id']))
            flow = asyncio.run(runtime.workflows.get(owner, flow['id']))
            ref = flow['object_ref']; assert flow['projection']['revision'] == 1
            assert asyncio.run(BusinessCenter(runtime).objects(owner, 'kit.review.workflow'))['items']
            outsider = asyncio.run(runtime.workflows.create(actors['outside'], 'kit.review.workflow', 'outside-open'))
            assert outsider['objects'] == []
            # Same object through the other surface, with optimistic revision.
            submitted = invoke(owner, 'submit', {'object_id': ref['object_id'], 'expected_revision': 1, 'command': {}}, 'submit-reference')
            assert submitted['data']['projection']['state'] == 'submitted'
            second = invoke(owner, 'submit', {'object_id': ref['object_id'], 'expected_revision': 1, 'command': {}}, 'submit-reference')
            assert second['data'] == submitted['data']
            runtime.access.grant('owner', 'kit.review', 'kit.review.reviewer', {'project_id': 'P', 'organization_id': 'org-a'}, actor_id='manager')
            denied = asyncio.run(runtime.invoke(owner, 'kit.review.approve', {'object_id': ref['object_id'], 'expected_revision': 2, 'command': {'note': '自审应拒绝'}}, 'self-review'))
            asyncio.run(runtime.process_run(denied['run_id']))
            with sessions() as db:
                from app.db import Run
                assert db.get(Run, denied['run_id']).status == 'failed'
            approved = invoke(reviewer, 'approve', {'object_id': ref['object_id'], 'expected_revision': 2, 'command': {'note': '已核对'}}, 'approve-reference')
            assert approved['data']['projection']['state'] == 'approved'
            # Owner grant change invalidated their old private flow, not the business object.
            with pytest.raises(PlatformError):
                asyncio.run(runtime.workflows.get(owner, flow['id']))
            fresh = asyncio.run(runtime.workflows.create(owner, 'kit.review.workflow', 'open-fresh'))
            fresh = asyncio.run(runtime.workflows.select(owner, fresh['id'], ref['object_id'], fresh['revision']))
            attached = runtime.workflows.attach(owner, fresh['id'])
            branch = ConversationService(runtime).fork(owner, attached['conversation_id'])
            assert branch['id'] != attached['conversation_id']
            assert asyncio.run(runtime.workflows.get(owner, fresh['id']))['projection']['revision'] == 3
            assert BusinessCenter(runtime).instances(owner)['items']
            read_spec = runtime.registry.get('kit.review.read', reviewer)
            ticket_request_id = secrets.token_hex(16)
            ticket = runtime.access_bridge.issue(reviewer, read_spec, ticket_request_id, 'objects.read')
            runtime.access.revoke(grants['reviewer']['id'], actor_id='manager')
            revoked = httpx.post(url + '/platform/v1/workflow/objects.read', headers={'Authorization': 'Bearer ' + token, 'X-Platform-Access': ticket}, json={
                'protocol_version': '1.0', 'request_id': ticket_request_id,
                'context': {'actor': runtime.workflows.transport._principal(reviewer)}, 'input': {'object_ref': ref}})
            assert revoked.status_code == 403, revoked.text
            binding = manager.get_binding('kit-reference')
            runtime.set_remote_publication('kit-reference', False, binding['revision'])
            with pytest.raises(PlatformError):
                asyncio.run(runtime.workflows.get(owner, fresh['id']))
            # An unchanged, accepted service reloads after restart. Removing or
            # changing a required host package revokes its saved acceptance.
            disabled = manager.list_services()['items'][0]
            runtime.set_remote_publication('kit-reference', True, disabled['revision'])
            reloaded = Runtime(base, sessions, SimpleNamespace(configured=False, redact=lambda value: value), manager, base / 'data')
            assert 'kit.review.read' in reloaded.registry.capabilities
            pack = kit.read_json(package, 'module-kit.json')['pack_name']
            installed_flow = base / 'flows/packs' / pack / 'flow.json'
            value = json.loads(installed_flow.read_text(encoding='utf-8'))
            value['flows'][0]['version'] = '99.0.0'
            write_json(installed_flow, value)
            changed = Runtime(base, sessions, SimpleNamespace(configured=False, redact=lambda value: value), manager, base / 'data')
            assert 'kit.review.read' not in changed.registry.capabilities
            assert not manager.list_services()['items'][0]['admission']['passed']
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        engine.dispose()


def test_generated_shared_components_typecheck_against_current_host_sdk(tmp_path):
    tool = generated(tmp_path)
    workflow = generated(tmp_path, 'workflow-node', 'kit.review')
    config = {'extends': str(ROOT / 'web/tsconfig.json'),
              'include': [str(tool / 'frontend/Component.tsx'), str(workflow / 'frontend/steps.tsx')]}
    write_json(tmp_path / 'tsconfig.json', config)
    result = subprocess.run([str(NODE), str(ROOT / 'web/node_modules/typescript/bin/tsc'), '-p', str(tmp_path / 'tsconfig.json')],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
