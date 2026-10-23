"""Registration is a publication gate, not a business correctness certificate."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.remote_services import RemoteServiceError, RemoteServiceManager
from app.registration_contract import CHECKS, make_offer, validate_registration
from registration_helpers import RegistrationPeer, declaration, sdk
from test_remote_services import manifest, connection, TOKEN


def setup_peer(tmp_path, clock=None):
    peer = RegistrationPeer(manifest(), TOKEN)
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(peer), clock=clock)
    manager.save_connection(connection(token=TOKEN))
    asyncio.run(manager.discover('remote-test'))
    return manager, peer


def test_discovery_alone_never_publishes_and_success_requires_every_check(tmp_path):
    manager, peer = setup_peer(tmp_path)
    with pytest.raises(RemoteServiceError, match='全部接入检查'):
        manager.set_enabled('remote-test', True, 1)
    result = asyncio.run(manager.admit('remote-test', 1))
    assert not result['enabled'] and result['admission']['passed']
    assert [c['id'] for c in result['admission']['checks']] == [key for key, _ in CHECKS]
    assert all(c['passed'] for c in result['admission']['checks'])
    manager.set_enabled('remote-test', True, 1)
    assert RemoteServiceManager(tmp_path).enabled_bindings()
    assert not peer.business_calls
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize('case,failed', [('public', 'authentication'), ('unhealthy', 'health'),
    ('empty', 'invoke'), ('nonce', 'module_confirmation'), ('digest', 'module_confirmation'),
    ('missing_handler', 'module_confirmation'), ('host', 'host'), ('registration', 'registration')])
def test_each_failed_check_denies_publication_and_never_dispatches_business(tmp_path, case, failed):
    manager, peer = setup_peer(tmp_path)
    host_check = None
    if case == 'public': peer.public = True
    elif case == 'unhealthy': peer.healthy = False
    elif case == 'empty': peer.accept_empty = True
    elif case in ('nonce', 'digest', 'missing_handler'):
        def change(answer):
            answer[{'nonce': 'nonce', 'digest': 'contract_digest', 'missing_handler': 'capability_ids'}[case]] = [] if case == 'missing_handler' else '0' * 32
            return answer
        peer.confirmation_change = change
    elif case == 'registration': peer.document['unknown_field'] = True
    else:
        def host_check(_): raise ValueError('private error and ' + TOKEN)
    result = asyncio.run(manager.admit('remote-test', 1, host_check=host_check))
    assert not result['admission']['passed'] and not result['enabled']
    assert next(c for c in result['admission']['checks'] if not c['passed'])['id'] == failed
    assert not peer.business_calls and TOKEN not in json.dumps(result)
    with pytest.raises(RemoteServiceError) as error:
        manager.set_enabled('remote-test', True, 1)
    assert error.value.code == 'remote_admission_required'


def test_failed_recheck_disables_published_module_and_changes_generation(tmp_path):
    manager, peer = setup_peer(tmp_path)
    asyncio.run(manager.admit('remote-test', 1)); manager.set_enabled('remote-test', True, 1)
    peer.healthy = False
    result = asyncio.run(manager.admit('remote-test', 2))
    assert result['revision'] == 3 and not result['enabled'] and not manager.enabled_bindings()
    assert not RemoteServiceManager(tmp_path).enabled_bindings()


def test_connection_change_and_old_publication_without_receipt_fail_closed(tmp_path):
    manager, _ = setup_peer(tmp_path)
    asyncio.run(manager.admit('remote-test', 1)); manager.set_enabled('remote-test', True, 1)
    changed = manager.save_connection({'id': 'remote-test', 'name': '新名称'})
    assert changed['admission'] is None and changed['registration'] is None and not changed['enabled']
    # A pre-upgrade connection file is retained for preview, not auto-published.
    state = manager._read(); item = state['services'][0]
    item.update(enabled=True, manifest=manifest(), discovered_revision=item['revision'])
    item.pop('admission'); item.pop('registration'); manager._write(state)
    restarted = RemoteServiceManager(tmp_path)
    assert not restarted.enabled_bindings() and not restarted.list_services()['items'][0]['enabled']


def test_expired_acceptance_needs_recheck_before_publication(tmp_path):
    now = [10]
    manager, _ = setup_peer(tmp_path, clock=lambda: now[0])
    asyncio.run(manager.admit('remote-test', 1)); now[0] += 301
    with pytest.raises(RemoteServiceError) as error: manager.set_enabled('remote-test', True, 1)
    assert error.value.code == 'remote_admission_required'


@pytest.mark.parametrize('change', ['connection', 'admin'])
def test_waiting_admission_cannot_commit_after_revision_or_admin_changes(tmp_path, change):
    manager, peer = setup_peer(tmp_path)
    def final_check():
        if change == 'admin': raise PermissionError('revoked')
    def mutate(answer):
        if change == 'connection': manager.save_connection({'id': 'remote-test', 'name': '变更'})
        return answer
    peer.confirmation_change = mutate
    with pytest.raises((RemoteServiceError, PermissionError)):
        asyncio.run(manager.admit('remote-test', 1, before_commit=final_check))
    assert manager.list_services()['items'][0]['admission'] is None


def test_python_module_actively_rejects_changed_offers_and_missing_handlers():
    m = manifest(); doc = declaration(m)
    registrar = sdk.Registration(m, doc, {m['capabilities'][0]['id']: lambda: None})
    offer = make_offer(m, doc, 'a' * 32)
    assert registrar.confirm(offer)['accepted']
    for field in ('manifest', 'registration', 'platform_features'):
        wrong = deepcopy(offer); wrong[field] = []
        with pytest.raises(sdk.RegistrationError): registrar.confirm(wrong)
    with pytest.raises(sdk.RegistrationError): sdk.Registration(m, doc, {})


@pytest.mark.parametrize('change', ['unknown', 'identity', 'foreign_capability', 'data_without_scope', 'missing_step'])
def test_registration_declared_content_is_checked_before_network_confirmation(change):
    m = manifest(); doc = declaration(m)
    if change == 'unknown': doc['providers'] = {}
    elif change == 'identity': doc['module_version'] = 'different'
    elif change == 'foreign_capability': m['capabilities'][0]['id'] = 'foreign.run'
    elif change == 'data_without_scope': doc['data_objects'] = [{'id': 'remote.echo.data', 'title': '合成', 'description': '合成数据', 'schema': {'type': 'object'}, 'operations': {'read': 'remote.echo.run'}, 'scope_fields': []}]
    else: doc['business_chains'] = [{'id': 'remote.echo.flow', 'title': '流程', 'description': '合成流程', 'entry_capability': 'missing.run', 'steps': ['missing.run']}]
    with pytest.raises(Exception): validate_registration(doc, m)


def test_simultaneous_admission_and_cancellation_release_the_connection_slot(tmp_path):
    async def scenario():
        manager, peer = setup_peer_async(tmp_path)
        await manager.discover('remote-test')
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(request):
            if request.url.path.endswith('/registration'):
                entered.set(); await release.wait()
            return peer(request)
        manager._transport = httpx.MockTransport(held)
        first = asyncio.create_task(manager.admit('remote-test', 1))
        await entered.wait()
        with pytest.raises(RemoteServiceError) as error:
            await manager.admit('remote-test', 1)
        assert error.value.code == 'remote_admission_busy'
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        assert not manager._admitting
        release.set()
        assert (await manager.admit('remote-test', 1))['admission']['passed']
    asyncio.run(scenario())


def setup_peer_async(tmp_path):
    peer = RegistrationPeer(manifest(), TOKEN)
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(peer))
    manager.save_connection(connection(token=TOKEN))
    return manager, peer


def test_python_adapter_handshake_over_real_asgi_routes_has_no_business_side_effects(tmp_path):
    from fastapi import FastAPI, Depends, HTTPException, Request
    app = FastAPI()
    m = manifest(); doc = declaration(m); calls = []
    registrar = sdk.Registration(m, doc, {m['capabilities'][0]['id']: lambda payload: calls.append(payload)})
    async def authenticate(request: Request):
        if request.headers.get('authorization') != 'Bearer ' + TOKEN:
            raise HTTPException(401)
    @app.get('/platform/v1/manifest', dependencies=[Depends(authenticate)])
    def get_manifest(): return registrar.manifest
    @app.get('/platform/v1/registration', dependencies=[Depends(authenticate)])
    def get_registration(): return registrar.document
    @app.get('/platform/v1/health', dependencies=[Depends(authenticate)])
    def health(): return {'status': 'ok'}
    @app.post('/platform/v1/handshake', dependencies=[Depends(authenticate)])
    async def handshake(request: Request):
        try: return registrar.confirm(await request.json())
        except sdk.RegistrationError: raise HTTPException(422) from None
    @app.post('/platform/v1/invoke', dependencies=[Depends(authenticate)])
    async def invoke(request: Request):
        # A real adapter validates the full invoke schema before any dispatch.
        if not (await request.json()).get('capability_id'): raise HTTPException(422)
        raise HTTPException(422)
    manager = RemoteServiceManager(tmp_path, transport=httpx.ASGITransport(app=app))
    manager.save_connection(connection(token=TOKEN, base_url='http://127.0.0.1:8039'))
    asyncio.run(manager.discover('remote-test'))
    result = asyncio.run(manager.admit('remote-test', 1))
    assert result['admission']['passed'] and not calls


@pytest.mark.parametrize('mutate', [lambda r: r.update(checks=[]), lambda r: r.update(checked_at=float('nan')),
                                   lambda r: r['checks'][0].update(passed=1)])
def test_corrupt_persisted_acceptance_never_reloads_as_published(tmp_path, mutate):
    manager, _ = setup_peer(tmp_path)
    asyncio.run(manager.admit('remote-test', 1)); manager.set_enabled('remote-test', True, 1)
    state = manager._read(); mutate(state['services'][0]['admission'])
    # Bypass the normal atomic writer solely to model a damaged local file.
    manager.path.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(RemoteServiceError) as error: RemoteServiceManager(tmp_path).enabled_bindings()
    assert error.value.code == 'remote_config_corrupt'
