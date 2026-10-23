import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.access_bridge import AccessBridge, create_access_bridge_router
from app.access_control import AccessControl
from app.db import User, create_database
from app.errors import PlatformError
from app.identity import load_actor
from app.registry import Registry
from test_access_control import definition


@pytest.fixture
def bridge_case(tmp_path):
    engine, sessions = create_database(tmp_path)
    clock = [1_800_000_000.0]
    with sessions() as db:
        db.add(User(id='alice', username='alice', name='合成用户', role='member', password_hash='unused', enabled=True))
        db.commit()
        actor = load_actor(db, 'alice')
    access = AccessControl(sessions, clock=lambda: clock[0])
    access.register_definition(definition(), actor_id='manager')
    binding = access.grant('alice', 'example-intake', 'example-intake.submitter',
                           {'project_id': 'P', 'owner_id': 'alice'}, actor_id='manager')
    spec = {'id': 'example-intake.command', 'version': '1.0', 'module_id': 'example-intake',
            'service_id': 'remote-example', 'binding_revision': 1, 'module_revision': 1}
    registry = Registry(tmp_path)
    registry.capabilities[spec['id']] = spec
    registry.access_allowed = lambda current, item: any(access.has_permission(current, p) for p in access.capability_permissions(item['id']))
    runtime = SimpleNamespace(sessions=sessions, access=access, registry=registry, availability=lambda item: {'available': True})
    bridge = AccessBridge(runtime, clock=lambda: clock[0])
    app = FastAPI()
    app.include_router(create_access_bridge_router(bridge))

    @app.exception_handler(PlatformError)
    async def handle(request, error):
        return JSONResponse({'error': error.code}, status_code=error.status)

    with TestClient(app) as client:
        yield SimpleNamespace(bridge=bridge, runtime=runtime, actor=actor, spec=spec, grant=binding,
                              clock=clock, client=client, sessions=sessions)
    engine.dispose()


def body(**extra):
    return {**dict(request_id='request-1', operation='invoke', permission='example-intake.edit',
                   facts={'project_id': 'P', 'owner_id': 'alice'}), **extra}


def decide(case, ticket, **extra):
    return case.client.post('/api/v1/access/remote-decisions', headers={'X-Platform-Access': ticket}, json=body(**extra))


def ticket(case):
    return case.bridge.issue(case.actor, case.spec, 'request-1', 'invoke')


def test_remote_ticket_uses_real_identity_casbin_and_exact_resource_scope(bridge_case):
    case = bridge_case
    token = ticket(case)
    result = decide(case, token)
    assert result.status_code == 200 and result.json()['allowed'] is True
    assert set(result.json()) == {'allowed', 'reason', 'policy_revision'}
    assert not decide(case, token, facts={'project_id': 'Q', 'owner_id': 'alice'}).json()['allowed']
    assert not decide(case, token, facts={'project_id': 'P', 'owner_id': 'bob'}).json()['allowed']
    assert token not in result.text and 'grant_ids' not in result.text


@pytest.mark.parametrize('changes,reason', [
    ({'request_id': 'request-other'}, 'access_ticket_request_mismatch'),
    ({'operation': 'objects.read'}, 'access_ticket_operation_mismatch'),
    ({'permission': 'other.read'}, 'access_ticket_permission_mismatch'),
    ({'permission': 'example-intake.read'}, 'access_ticket_permission_mismatch'),
])
def test_ticket_cannot_switch_request_operation_module_or_capability(bridge_case, changes, reason):
    result = decide(bridge_case, ticket(bridge_case), **changes).json()
    assert not result['allowed'] and result['reason'] == reason


def test_ticket_tampering_expiry_and_restart_are_denied(bridge_case):
    case = bridge_case
    token = ticket(case)
    tampered = ('A' if token[0] != 'A' else 'B') + token[1:]
    assert not decide(case, tampered).json()['allowed']
    other_process = AccessBridge(case.runtime, clock=lambda: case.clock[0])
    assert not other_process.decide(token, 'request-1', 'example-intake.edit', body()['facts'])['allowed']
    case.clock[0] += 45
    assert decide(case, token).json()['reason'] == 'access_ticket_expired'


def test_fresh_role_revocation_and_service_binding_rechecked(bridge_case):
    case = bridge_case
    token = ticket(case)
    case.spec['binding_revision'] += 1
    assert decide(case, token).json()['reason'] == 'access_bridge_binding'
    token = ticket(case)
    case.runtime.access.revoke(case.grant['id'], actor_id='manager')
    assert decide(case, token).json()['reason'] == 'access_authorization_changed'


def test_disabled_user_cannot_keep_using_ticket(bridge_case):
    case = bridge_case
    token = ticket(case)
    with case.sessions() as db:
        db.get(User, 'alice').enabled = False
        db.commit()
    assert decide(case, token).json()['reason'] == 'actor_disabled'


def test_service_unavailable_and_browser_cookie_are_not_authorization(bridge_case):
    case = bridge_case
    token = ticket(case)
    case.runtime.availability = lambda item: {'available': False}
    assert decide(case, token).json()['reason'] == 'access_bridge_unavailable'
    result = case.client.post('/api/v1/access/remote-decisions', json=body(), headers={'Cookie': 'common_framework_session=fake'})
    assert result.status_code == 200 and not result.json()['allowed']


@pytest.mark.parametrize('extra', [
    {'actor': {'id': 'another-user'}}, {'facts': {'project_id': {'nested': 'P'}}},
    {'facts': {'project_id': ['P', 1]}}, {'operation': None}, {'facts': {'project_id': 'x' * 161}},
])
def test_decision_request_strictly_bounded_and_has_no_identity_override(bridge_case, extra):
    assert decide(bridge_case, ticket(bridge_case), **extra).status_code == 422


def test_request_size_bounded_before_json_parse(bridge_case):
    response = bridge_case.client.post('/api/v1/access/remote-decisions', content=b' ' * 16385)
    assert response.status_code == 413


def test_node_client_fail_closed_and_fixed_callback_configuration():
    node = shutil.which('node')
    assert node, 'Node.js is required to validate the cross-language client'
    source = (Path(__file__).resolve().parents[1] / 'templates' / 'remote-access-client' / 'node.mjs').as_uri()
    program = '''
import assert from 'node:assert/strict';
const { createAccessClient } = await import(process.argv[1]);
const callbackUrl = 'http://127.0.0.1:8030/api/v1/access/remote-decisions';
const input = { ticket:'synthetic-ticket', requestId:'r1', operation:'invoke', permission:'example.read', facts:{project_id:'P'} };
let seen;
const okay = createAccessClient({ callbackUrl, fetchImpl:async (url,options)=>{
 seen = {url:String(url),options}; return new Response(JSON.stringify({allowed:true,reason:'allowed',policy_revision:1}));
}});
assert.equal((await okay(input)).allowed,true);
assert.equal(seen.url,callbackUrl);
assert.equal(seen.options.headers['X-Platform-Access'],'synthetic-ticket');
assert.equal(seen.options.redirect,'error');
for (const mock of [async()=>{throw Error('offline')},async()=>new Response('bad',{status:503}),async()=>new Response('{'),async()=>new Response('x'.repeat(5000)),async()=>new Response(JSON.stringify({allowed:'true',reason:'bad',policy_revision:1}))]) {
 assert.equal((await createAccessClient({callbackUrl,fetchImpl:mock})(input)).allowed,false);
}
assert.throws(()=>createAccessClient({callbackUrl:'file:///secret'}));
assert.throws(()=>createAccessClient({callbackUrl:'http://user:password@localhost/api/v1/access/remote-decisions'}));
assert.equal((await okay({...input,ticket:''})).allowed,false);
console.log('Node client: fixed callback, bounded responses, failure denial passed');
'''
    result = subprocess.run([node, '--input-type=module', '-e', program, source], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'passed' in result.stdout
