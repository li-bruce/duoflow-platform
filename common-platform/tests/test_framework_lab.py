"""Real local workflow, isolated SQLite and host routes; no external service."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.conversation import ConversationService
from app.db import Audit, IdentityProfile, Invocation, User, create_database, user_dict
from app.errors import PlatformError
from app.runtime import Runtime
from app.workflow_routes import create_workflow_router

ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/v1/flows'
FLOW_ID = 'framework-lab.workflow'


@pytest.fixture
def host(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    for kind, filename, value in [('extensions', 'installed.json', {'modules': ['framework-lab']}),
                                  ('flows', 'installed.json', {'packs': ['framework-lab']})]:
        target = tmp_path / kind
        target.mkdir()
        (target / filename).write_text(json.dumps(value), encoding='utf-8')
    shutil.copytree(ROOT / 'extensions/framework-lab', tmp_path / 'extensions/framework-lab')
    shutil.copytree(ROOT / 'flows/packs/framework-lab', tmp_path / 'flows/packs/framework-lab')
    engine, sessions = create_database(tmp_path / 'data')
    with sessions.begin() as db:
        users = [User(username=name, name=name, role=role, password_hash='unused') for name, role in
                 [('alice', 'member'), ('bob', 'member'), ('system', 'system_admin'), ('legacy', 'admin')]]
        db.add_all(users); db.flush()
        for user in users: db.add(IdentityProfile(user_id=user.id, roles=[user.role], authorization_revision=1))
        db.flush(); actors = {user.username: user_dict(user) for user in users}
    gateway = SimpleNamespace(configured=False, redact=lambda value: value)
    runtime = Runtime(tmp_path, sessions, gateway, data_dir=tmp_path / 'data')
    spec = importlib.util.spec_from_file_location('framework_lab_test_module', tmp_path / 'extensions/framework-lab/handlers.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = FastAPI()
    def actor(x_actor: str = Header(default='alice')): return actors[x_actor]
    @app.exception_handler(PlatformError)
    async def error(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)
    app.include_router(create_workflow_router(runtime.workflows, actor, actor))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, runtime=runtime, sessions=sessions, actors=actors,
                              module=module, root=tmp_path, gateway=gateway)
    engine.dispose()


def open_flow(host, key='flow-one', who='alice'):
    response = host.client.post(BASE + '/instances', json={'flow_id': FLOW_ID, 'idempotency_key': key}, headers={'X-Actor': who})
    assert response.status_code == 200, response.text
    return response.json()


def body(item, command, key):
    return {'command': command, 'expected_revision': item['revision'],
            'expected_object_revision': item['projection']['revision'] if item['projection'] else None,
            'idempotency_key': key}


def act(host, item, action, command=None, key=None, who='alice'):
    response = host.client.post(f"{BASE}/instances/{item['id']}/actions/{action}",
        json=body(item, command or {}, key or action + '-' + str(item['revision'])), headers={'X-Actor': who})
    assert response.status_code == 200, response.text
    return response.json()['instance']


def navigate(host, item, step):
    response = host.client.patch(f"{BASE}/instances/{item['id']}", json={'cursor': step,
        'drafts': item['drafts'], 'expected_revision': item['revision']})
    assert response.status_code == 200, response.text
    return response.json()


def make(host, who='alice'):
    return act(host, open_flow(host, who=who), 'create', {'title': '中立工作项', 'body': '用于验证真实状态和修订。'}, who=who)


def test_host_loads_real_modules_and_same_fsm_declaration(host):
    catalog = host.client.get(BASE + '/catalog').json()['items']
    assert len(catalog) == 1 and catalog[0]['id'] == FLOW_ID
    assert catalog[0]['state_machine'] == host.module.MACHINE
    assert {entry['id'] for entry in host.module.MACHINE['states']} == {'draft', 'submitted', 'returned', 'completed'}
    for who in ['alice', 'system', 'legacy']:
        assert host.client.get(BASE + '/catalog', headers={'X-Actor': who}).json()['items'][0]['available']


def test_return_resubmit_complete_tracks_actual_declared_transitions(host):
    item = make(host)
    assert item['projection']['state'] == 'draft'
    item = act(host, item, 'submit')
    assert item['projection']['state'] == 'submitted' and item['cursor'] == 'inspect'
    item = act(host, item, 'return', {'note': '补充说明再提交'})
    assert item['projection']['state'] == 'returned' and item['cursor'] == 'edit'
    item = act(host, item, 'edit', {'title': '已补充', 'body': '第二版工作项内容。'})
    assert item['projection']['state'] == 'returned'
    item = act(host, item, 'submit')
    item = act(host, item, 'complete', {'note': '本次调试完成'})
    p = item['projection']
    assert p['state'] == 'completed' and p['revision'] == 6 and not p['allowed_actions']
    assert [entry['action'] for entry in p['data']['history']] == ['create','submit','return','edit','submit','complete']
    transitions = {entry['id']: entry for entry in host.module.MACHINE['transitions']}
    for entry in p['data']['history'][1:]:
        declared = transitions[entry['transition_id']]
        assert (entry['from'], entry['to'], entry['action']) == (declared['from'], declared['to'], declared['action_id'])
    with host.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action.like('framework-lab.%'),
            Audit.target == p['object_ref']['object_id'])) == 6


def test_duplicate_command_after_navigation_and_restart_has_one_object_and_receipt(host):
    fresh = open_flow(host)
    payload = body(fresh, {'title': '同一请求', 'body': '幂等验证'}, 'stable-create')
    url = f"{BASE}/instances/{fresh['id']}/actions/create"
    first = host.client.post(url, json=payload)
    second = host.client.post(url, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()['result']['id'] == second.json()['result']['id']
    restored = Runtime(host.root, host.sessions, host.gateway, data_dir=host.root / 'data')
    item = first.json()['instance']
    result = asyncio.run(restored.workflows.get(host.actors['alice'], item['id']))
    assert result['projection']['object_ref'] == item['projection']['object_ref']
    assert result['projection']['revision'] == 1
    with host.sessions() as db:
        assert db.scalar(select(func.count()).select_from(host.module.records)) == 1
        assert db.scalar(select(func.count()).select_from(Invocation).where(Invocation.capability_id == 'framework-lab.create')) == 1


@pytest.mark.parametrize('who', ['bob','system','legacy'])
def test_owner_scope_has_no_admin_bypass(host, who):
    item = make(host)
    assert host.client.get(f"{BASE}/instances/{item['id']}", headers={'X-Actor': who}).status_code == 403
    own = open_flow(host, key='other', who=who)
    assert own['objects'] == []
    response = host.client.post(f"{BASE}/instances/{own['id']}/select", json={
        'object_id': item['projection']['object_ref']['object_id'], 'expected_revision': own['revision']}, headers={'X-Actor': who})
    assert response.status_code == 404
    with host.sessions.begin() as db:
        ctx = SimpleNamespace(session=db, actor=host.actors[who])
        with pytest.raises(PlatformError) as caught:
            host.module.submit_item(ctx, {'object_id':item['object_ref']['object_id'], 'expected_revision':1, 'command':{}})
        assert caught.value.status == 404


def test_old_revision_and_forged_state_do_not_overwrite(host):
    item = make(host)
    identifier = item['object_ref']['object_id']
    changed = act(host, item, 'edit', {'title':'新版本','body':'已经保存的说明。'})
    with host.sessions.begin() as db:
        ctx = SimpleNamespace(session=db, actor=host.actors['alice'])
        with pytest.raises(PlatformError) as caught:
            host.module.submit_item(ctx, {'object_id':identifier,'expected_revision':1,'command':{}})
        assert caught.value.code == 'revision_conflict'
    bad = host.client.post(f"{BASE}/instances/{changed['id']}/actions/submit",
        json=body(changed, {'state':'completed'}, 'forge-state'))
    assert bad.status_code == 422
    changed = navigate(host, changed, 'inspect')
    assert changed['projection']['state'] == 'draft'
    with host.sessions.begin() as db:
        ctx = SimpleNamespace(session=db, actor=host.actors['alice'])
        with pytest.raises(PlatformError) as caught:
            host.module.complete_item(ctx, {'object_id':identifier,'expected_revision':2,'command':{'note':'forged'}})
        assert caught.value.code == 'state_conflict'


def test_validation_failure_keeps_business_state_and_audit_unchanged(host):
    item = act(host, open_flow(host), 'create', {'title':'没有说明','body':''})
    with host.sessions.begin() as db:
        ctx = SimpleNamespace(session=db, actor=host.actors['alice'])
        with pytest.raises(PlatformError) as caught:
            host.module.submit_item(ctx, {'object_id':item['object_ref']['object_id'],'expected_revision':1,'command':{}})
        assert caught.value.code == 'body_required'
    with host.sessions() as db:
        row = host.module.load(db, host.actors['alice'], item['object_ref']['object_id'])
        assert row['state'] == 'draft' and row['revision'] == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'framework-lab.submit')) == 0


def test_rollback_keeps_module_object_and_audit_atomic(host):
    with host.sessions.begin() as db:
        ctx = SimpleNamespace(session=db, actor=host.actors['alice'])
        host.module.create_item(ctx, {'command':{'title':'回滚验证','body':'不会提交'}})
        db.rollback()
    with host.sessions() as db:
        assert db.scalar(select(func.count()).select_from(host.module.records)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'framework-lab.create')) == 0


def test_page_chat_recovery_and_branch_preserve_authority_without_writes(host):
    item = make(host)
    attached = host.client.post(f"{BASE}/instances/{item['id']}/attach").json()
    recovered = host.client.get(f"{BASE}/instances/{item['id']}").json()
    assert recovered['conversation_id'] == attached['conversation_id']
    assert recovered['projection'] == item['projection']
    conversations = ConversationService(host.runtime)
    conversation = conversations.get(host.actors['alice'], attached['conversation_id'])
    branch = conversations.fork(host.actors['alice'], attached['conversation_id'], conversation['messages'][-1]['id'])
    child = branch['messages'][-1]['views'][0]['data']['instance_id']
    cloned = host.client.get(f'{BASE}/instances/{child}').json()
    assert child != item['id'] and cloned['object_ref'] == item['object_ref'] and cloned['projection']['revision'] == 1
    with host.sessions() as db:
        assert db.scalar(select(func.count()).select_from(host.module.records)) == 1


def test_directory_cursor_does_not_cross_owner_or_stale_snapshot(host):
    for n in range(3):
        with host.sessions.begin() as db:
            host.module.create_item(SimpleNamespace(session=db,actor=host.actors['alice']),
                                    {'command':{'title':str(n),'body':'说明'}})
    with host.sessions() as db:
        first = host.module.listing(db,host.actors['alice'],{'limit':2})
        assert len(first['objects']) == 2 and first['next_cursor']
        with pytest.raises(PlatformError):
            host.module.listing(db,host.actors['bob'],{'cursor':first['next_cursor']})
    with host.sessions.begin() as db:
        host.module.create_item(SimpleNamespace(session=db,actor=host.actors['alice']),{'command':{'title':'新增','body':'说明'}})
    with host.sessions() as db:
        with pytest.raises(PlatformError) as caught:
            host.module.listing(db,host.actors['alice'],{'cursor':first['next_cursor']})
        assert caught.value.code == 'revision_conflict'
