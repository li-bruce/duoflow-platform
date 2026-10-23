"""Real LangGraph/checkpointer tests; all identities, results and credentials are synthetic."""
import asyncio
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.agent_engine import AgentEngine
from app.db import Conversation, Invocation, Message, Run, User, create_database, now, uid
from app.errors import PlatformError, require
from app.identity import authorization_snapshot, load_actor
from app.registry import Registry
from app.runtime import Runtime


def call(engine, actor, cid, content):
    return asyncio.run(engine.prepare(actor, cid, content))


def flow(response):
    return next(v['data'] for v in response['views'] if v['type'] == 'agent-flow')


def forms(response):
    return [v['data'] for v in response['views'] if v['type'] == 'form']


@pytest.fixture
def platform(tmp_path):
    db_engine, sessions = create_database(tmp_path, database_url='sqlite:///' + (tmp_path / 'test.sqlite3').as_posix())
    with sessions.begin() as db:
        db.add_all([User(id='owner', username='owner', name='合成用户', role='member', password_hash='unused'),
                    User(id='other', username='other', name='其他用户', role='member', password_hash='unused')])
        db.add(Conversation(id='conversation', owner_id='owner', title='合成对话'))
    runtime = Runtime.__new__(Runtime)
    runtime.sessions = sessions
    runtime.remote_services = None
    runtime.gateway = SimpleNamespace(configured=False, redact=lambda text: text.replace('synthetic-configured-secret', '[REDACTED]'))
    runtime.registry = Registry(tmp_path)
    skill = {'id': 'process', 'title': '合成多步流程', 'description': '测试声明，不包含业务特例。', 'triggers': ['合成流程'],
             'entry_capability': 'test.start',
             'steps': [{'id': name, 'title': title, 'capability_id': 'test.' + name}
                       for name, title in [('start', '准备'), ('review', '核对'), ('finish', '完成')]]}
    module = {'id': 'test.module', 'name': '合成模块', 'description': '', 'version': '1.0.0', 'revision': 1,
              'surfaces': {'page_tools': [], 'conversation': {'capability_ids': ['test.start', 'test.review', 'test.finish'],
                                                             'starter_prompts': [], 'skills': [skill]}}}
    runtime.registry.modules.append(module)
    for name in ('start', 'review', 'finish'):
        spec = {'id': 'test.' + name, 'version': '1.0.0', 'title': name, 'description': '', 'kind': 'command',
                'roles': ['member'], 'execution': {'mode': 'sync', 'confirmation': True},
                'presentation': {host: {'renderer': 'workspace'} for host in ('page', 'conversation')},
                'input_schema': {'type': 'object', 'properties': {'reference': {'type': 'string'}, 'note': {'type': 'string'}}, 'additionalProperties': False},
                'output_schema': {'type': 'object'}}
        runtime.registry.register(module, spec, lambda *_: pytest.fail('A guidance graph must not call a business handler.'))
    runtime.registry.availability = lambda _: {'state': 'local', 'available': True}
    offline = set()
    runtime.check_available = lambda spec: require(spec['id'] not in offline, 'remote_offline', '合成服务离线。', 503)
    runtime.invoke = lambda *_args, **_kw: pytest.fail('Guidance must never invoke business operations.')
    with sessions() as db:
        actor = load_actor(db, 'owner')
    engine = AgentEngine(runtime, tmp_path)
    yield SimpleNamespace(runtime=runtime, actor=actor, cid='conversation', engine=engine, path=tmp_path,
                          skill=skill, module=module, offline=offline)
    engine.close()
    db_engine.dispose()


def completed(platform, capability, *, cid=None, actions=(), status='completed', run_status=None, invocation_id=None, created_at=None):
    result = {'summary': '合成结果', 'data': {'blocks': [{'type': 'actions', 'items': list(actions)}]},
              'views': [{'type': 'workspace', 'capability_id': capability, 'authorization_revision': 1,
                         'data': {'blocks': [{'type': 'actions', 'items': list(actions)}]}}]}
    invocation_id = invocation_id or uid()
    with platform.runtime.sessions.begin() as db:
        invocation = Invocation(id=invocation_id, actor_id=platform.actor['id'], capability_id=capability,
                                capability_version=platform.runtime.execution_revision(platform.runtime.registry.capabilities[capability]),
                                idempotency_key=uid(), input_hash='0' * 64, input={},
                                principal_snapshot=authorization_snapshot(platform.actor), origin='conversation',
                                conversation_id=cid or platform.cid, status=status, result=result, created_at=created_at or now())
        db.add(invocation)
        if run_status:
            run_id = uid()
            db.add(Run(id=run_id, invocation_id=invocation_id, actor_id=platform.actor['id'], capability_id=capability,
                       capability_version=invocation.capability_version, status=run_status, input={},
                       result=result if run_status == 'succeeded' else None))
    return invocation_id


def persist(platform, response, *, cid=None, message_id=None, created_at=None):
    with platform.runtime.sessions.begin() as db:
        message = Message(id=message_id or uid(), conversation_id=cid or platform.cid, role='assistant', content=response['content'],
                          views=deepcopy(response['views']), request_key=uid(), created_at=created_at or now())
        db.add(message)
        db.flush()
        return message.id


def test_real_graph_prepares_only_on_explicit_entry_and_has_sqlite_checkpoint(platform):
    p = platform
    assert call(p.engine, p.actor, p.cid, '请介绍合成流程，但不要开始') is None
    assert call(p.engine, p.actor, p.cid, '合成流程有什么特点') is None
    assert p.engine.graph.get_state(p.engine._config(p.actor['id'], p.cid)).values == {}
    result = call(p.engine, p.actor, p.cid, '开始合成流程')
    assert forms(result) == [{'capability_id': 'test.start', 'input': {}}]
    assert flow(result)['current_step'] == 0 and flow(result)['engine'] == 'langgraph'
    assert {'resolve', 'authorize', 'prepare_form', 'reply'} <= set(p.engine.graph.get_graph().nodes)
    with sqlite3.connect(p.engine.path) as db:
        assert db.execute('select count(*) from checkpoints').fetchone()[0] > 0
    with p.runtime.sessions() as db:
        assert db.query(Invocation).count() == 0


def test_multi_step_success_actions_are_proposals_and_progress_is_idempotent(platform):
    p = platform
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.start', actions=[{'label': '下一步', 'capability_id': 'test.review', 'input': {'reference': 'synthetic-result-42', 'ignored': 'filtered'}},
                                        {'label': '其他能力', 'capability_id': 'test.finish', 'input': {'note': 'not-yet'}}])
    result = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(result)['current_step'] == 1
    assert forms(result) == [{'capability_id': 'test.review', 'input': {'reference': 'synthetic-result-42'}}]
    again = call(p.engine, p.actor, p.cid, '继续')
    assert flow(again)['current_step'] == 1
    completed(p, 'test.review', actions=[{'label': '完成', 'capability_id': 'test.finish', 'input': {'reference': 'next-42'}}])
    assert forms(call(p.engine, p.actor, p.cid, '下一步'))[0]['input'] == {'reference': 'next-42'}
    completed(p, 'test.finish')
    result = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(result)['status'] == 'completed' and not forms(result)
    assert len(flow(result)['completed_invocations']) == 3
    with p.runtime.sessions() as db:
        assert db.query(Invocation).count() == 3


def test_restart_restores_checkpoint_without_replaying_inputs(platform):
    p = platform
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.start')
    initial = call(p.engine, p.actor, p.cid, '/continue')
    p.engine.close()
    p.engine = AgentEngine(p.runtime, p.path)
    restored = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(restored)['flow_id'] == flow(initial)['flow_id']
    assert flow(restored)['current_step'] == 1
    assert 'test.review' in p.engine.context_hint(p.actor, p.cid)
    with p.runtime.sessions() as db:
        assert db.query(Invocation).count() == 1


def test_fork_uses_copied_message_anchor_not_parents_future_checkpoint(platform):
    p = platform
    early = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    anchor = persist(p, early)
    completed(p, 'test.start')
    late = call(p.engine, p.actor, p.cid, '/continue')
    persist(p, late)
    with p.runtime.sessions.begin() as db:
        db.add(Conversation(id='child', owner_id=p.actor['id'], parent_id=p.cid, fork_message_id=anchor))
        db.add(Message(conversation_id='child', role='assistant', content=early['content'],
                       views=deepcopy(early['views']), request_key='fork-anchor'))
    child = call(p.engine, p.actor, 'child', '/continue')
    assert flow(child)['current_step'] == 0 and flow(child)['conversation_id'] == 'child'
    completed(p, 'test.review')
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 2
    assert flow(call(p.engine, p.actor, 'child', '/continue'))['current_step'] == 0
    completed(p, 'test.start', cid='child')
    assert flow(call(p.engine, p.actor, 'child', '/continue'))['current_step'] == 1
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 2


@pytest.mark.parametrize('id_offset', range(4))
def test_exit_tombstone_prevents_resurrection_from_old_message_on_fork(platform, id_offset):
    p = platform
    initial = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    exited = call(p.engine, p.actor, p.cid, '/exit')
    assert flow(exited)['status'] == 'exited'
    with p.runtime.sessions.begin() as db:
        db.add(Conversation(id='exit-child', owner_id=p.actor['id']))
    # Force equal wall time and UUID order opposite to actual insertion order.
    stamp = '2026-09-23T01:02:03.000000+00:00'
    persist(p, initial, cid='exit-child', message_id=f'{1000 + id_offset:032x}', created_at=stamp)
    persist(p, exited, cid='exit-child', message_id=f'{id_offset:032x}', created_at=stamp)
    assert call(p.engine, p.actor, 'exit-child', '/continue')['views'] == []
    assert p.engine.context_hint(p.actor, p.cid) == ''


def test_cross_owner_stale_identity_and_disabled_account_are_rejected(platform):
    p = platform
    with p.runtime.sessions() as db:
        other = load_actor(db, 'other')
    for content in ('hello', '/skills', '/skill test.module/process'):
        with pytest.raises(PlatformError, match='无权访问'):
            call(p.engine, other, p.cid, content)
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    with p.runtime.sessions.begin() as db:
        db.get(User, 'owner').role = 'system_admin'
    with pytest.raises(PlatformError) as error:
        call(p.engine, p.actor, p.cid, '/continue')
    assert error.value.code == 'authorization_changed'
    with p.runtime.sessions() as db:
        current = load_actor(db, 'owner')
    assert '停用' in call(p.engine, current, p.cid, '/continue')['content']
    with p.runtime.sessions.begin() as db:
        db.get(User, 'owner').enabled = False
    with pytest.raises(PlatformError) as error:
        call(p.engine, current, p.cid, '/skills')
    assert error.value.code == 'actor_disabled'


def test_required_unauthorized_step_is_preserved_and_blocks_not_skipped(platform):
    p = platform
    p.runtime.registry.capabilities['test.review']['roles'] = ['reviewer']
    result = call(p.engine, p.actor, p.cid, '/skills')
    assert len(flow(result)['skills']) == 1
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.start')
    blocked = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(blocked)['status'] == 'blocked'
    assert flow(blocked)['blocked_reason'] == 'permission'
    assert len(flow(blocked)['steps']) == 3 and flow(blocked)['current_step'] == 1
    assert forms(blocked) == [] and '需要相应权限' in blocked['content']
    hint = p.engine.context_hint(p.actor, p.cid)
    metadata = json.loads(hint.split('：', 1)[1].split('\n', 1)[0])
    assert 'test.review' not in metadata['allowed_capability_ids']
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 1


def test_module_withdrawal_revision_change_and_offline_do_not_retarget_old_flow(platform):
    p = platform
    original = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    p.offline.add('test.start')
    blocked = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(blocked)['status'] == 'blocked' and not forms(blocked)
    p.offline.clear()
    assert forms(call(p.engine, p.actor, p.cid, '/continue'))
    p.runtime.registry.modules.clear()
    assert call(p.engine, p.actor, p.cid, '/continue')['views'] == []
    with pytest.raises(PlatformError):
        p.engine.refresh_view(p.actor, p.cid, flow(original))
    p.runtime.registry.modules.append(p.module)
    p.module['revision'] = 2
    assert '版本已变化' in call(p.engine, p.actor, p.cid, '/continue')['content']
    fresh = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    assert flow(fresh)['flow_id'] != flow(original)['flow_id']


def test_invalid_ambiguous_and_no_skills_have_explicit_outcomes(platform):
    p = platform
    for command in ('/skill invalid', '/skill test.module/missing'):
        result = call(p.engine, p.actor, p.cid, command)
        assert result['content'] and result['views'] == []
    duplicate = deepcopy(p.skill)
    duplicate['id'] = 'other'
    p.module['surfaces']['conversation']['skills'].append(duplicate)
    ambiguous = call(p.engine, p.actor, p.cid, '合成流程')
    assert len(flow(ambiguous)['skills']) == 2 and not forms(ambiguous)
    p.module['surfaces']['conversation']['skills'] = [{**p.skill, 'executable_script': 'disallowed'}]
    assert '声明无效' in call(p.engine, p.actor, p.cid, '/skill test.module/process')['content']
    p.module['surfaces']['conversation']['skills'] = []
    assert flow(call(p.engine, p.actor, p.cid, '/skills'))['skills'] == []


@pytest.mark.parametrize('status,expected', [('running', 'awaiting_result'), ('failed', 'failed'), ('cancelled', 'failed')])
def test_background_incomplete_never_advances_or_replays(platform, status, expected):
    p = platform
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.start', run_status=status)
    response = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(response)['status'] == expected and flow(response)['current_step'] == 0
    assert not forms(response) and any(v['type'] == 'task' for v in response['views'])


@pytest.mark.parametrize('id_offset', range(4))
def test_earlier_out_of_order_result_cannot_complete_later_step(platform, monkeypatch, id_offset):
    p = platform
    stamp = '2026-09-23T01:02:03.000000+00:00'
    monkeypatch.setattr('app.agent_engine.now', lambda: stamp)
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.review', invocation_id=f'{1000 + id_offset:032x}', created_at=stamp)
    completed(p, 'test.start', invocation_id=f'{id_offset:032x}', created_at=stamp)
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 1
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 1


def test_flow_entry_snapshot_excludes_preexisting_invocation_at_identical_time(platform, monkeypatch):
    p = platform
    stamp = '2026-09-23T01:02:03.000000+00:00'
    monkeypatch.setattr('app.agent_engine.now', lambda: stamp)
    old_id = completed(p, 'test.start', invocation_id='f' * 32, created_at=stamp)
    initial = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    assert flow(initial)['start_after_invocation_id'] == old_id
    assert flow(initial)['event_order'] == 'sqlite-insertion-v1'
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 0
    new_id = completed(p, 'test.start', invocation_id='0' * 32, created_at=stamp)
    progressed = call(p.engine, p.actor, p.cid, '/continue')
    assert flow(progressed)['current_step'] == 1
    assert flow(progressed)['completed_invocations'] == [new_id]


def test_cursor_resolves_record_id_after_rowid_reassignment_and_fails_if_missing(platform):
    p = platform
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    first = completed(p, 'test.start')
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 1
    # Simulate maintenance changing numeric rowids while retaining record order.
    # No checkpoint stores these mutable numbers.
    from sqlalchemy import text
    with p.runtime.sessions.begin() as db:
        db.execute(text('UPDATE invocations SET rowid = 100 WHERE id = :id'), {'id': first})
    second = completed(p, 'test.review')
    assert flow(call(p.engine, p.actor, p.cid, '/continue'))['current_step'] == 2
    with p.runtime.sessions.begin() as db:
        db.delete(db.get(Invocation, second))
    with pytest.raises(PlatformError) as error:
        call(p.engine, p.actor, p.cid, '/continue')
    assert error.value.code == 'skill_cursor_missing'


def test_real_conversation_fork_equal_timestamps_preserves_anchor_and_committed_projection(platform):
    from app.conversation import ConversationService
    p = platform
    service = ConversationService(p.runtime, agent_engine=p.engine)
    stamp = '2026-09-23T01:02:03.000000+00:00'
    initial = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    anchor_id = persist(p, initial, message_id='f' * 32, created_at=stamp)
    completed(p, 'test.start')
    advanced = call(p.engine, p.actor, p.cid, '/continue')
    persist(p, advanced, message_id='8' * 32, created_at=stamp)
    exited = call(p.engine, p.actor, p.cid, '/exit')
    persist(p, exited, message_id='0' * 32, created_at=stamp)
    parent = service.get(p.actor, p.cid)
    assert [flow(m)['status'] for m in parent['messages']] == ['awaiting_input', 'awaiting_input', 'exited']

    anchored = service.fork(p.actor, p.cid, anchor_id)
    assert anchored == service.get(p.actor, anchored['id'])
    assert len(anchored['messages']) == 1
    assert flow(anchored['messages'][0])['conversation_id'] == anchored['id']
    assert flow(call(p.engine, p.actor, anchored['id'], '/continue'))['current_step'] == 0

    full = service.fork(p.actor, p.cid)
    assert full == service.get(p.actor, full['id'])
    assert [flow(m)['status'] for m in full['messages']] == ['awaiting_input', 'awaiting_input', 'exited']
    assert call(p.engine, p.actor, full['id'], '/continue')['views'] == []
    assert p.engine.context_hint(p.actor, full['id']) == ''


def test_legacy_unordered_flow_requires_reselection_and_non_sqlite_is_explicit(platform):
    from app.agent_engine import _sqlite_order
    p = platform
    initial = call(p.engine, p.actor, p.cid, '/skill test.module/process')
    legacy = deepcopy(flow(initial))
    legacy.pop('event_order')
    with pytest.raises(PlatformError) as error:
        p.engine.refresh_view(p.actor, p.cid, legacy)
    assert error.value.code == 'skill_order_changed'
    with pytest.raises(PlatformError) as error:
        _sqlite_order(SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))), Invocation)
    assert error.value.code == 'skill_order_unsupported'


def test_checkpoint_omits_business_inputs_and_langsmith_trace_is_disabled(platform, monkeypatch):
    p = platform
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'true')
    import langsmith.client
    monkeypatch.setattr(langsmith.client.Client, 'create_run', lambda *_a, **_kw: pytest.fail('No remote tracing allowed.'))
    call(p.engine, p.actor, p.cid, '/skill test.module/process')
    completed(p, 'test.start', actions=[{'label': '秘密', 'capability_id': 'test.review', 'input': {'note': 'synthetic-configured-secret'}},
                                      {'label': '安全引用', 'capability_id': 'test.review', 'input': {'reference': 'sensitive-business-value-109'}}])
    response = call(p.engine, p.actor, p.cid, '/continue')
    assert forms(response) == [{'capability_id': 'test.review', 'input': {'reference': 'sensitive-business-value-109'}}]
    assert 'synthetic-configured-secret' not in json.dumps(response)
    assert 'sensitive-business-value-109' not in p.engine.context_hint(p.actor, p.cid)
    p.engine.close()
    saved = p.engine.path.read_bytes()
    assert b'synthetic-configured-secret' not in saved and b'sensitive-business-value-109' not in saved


def test_real_create_app_skill_http_route_uses_engine_before_configured_model(tmp_path, monkeypatch):
    """A paid/default model must never absorb an explicit registered skill command."""
    import httpx
    from fastapi.testclient import TestClient
    # The module exposes a default app; isolate its import-time initialization too.
    monkeypatch.setenv('PLATFORM_DATA_DIR', str(tmp_path / 'import-app'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    from app.main import create_app
    from app.remote_services import RemoteServiceManager

    module_id, cap_id = 'synthetic.skill', 'synthetic.skill.start'
    manifest = {'protocol_version': '1.2', 'module': {'id': module_id, 'name': '合成技能服务', 'version': '1.0.0', 'description': '端到端测试'},
                'capabilities': [{'id': cap_id, 'title': '合成起点', 'description': '测试', 'version': '1.0.0', 'kind': 'command',
                                  'input_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'additionalProperties': False},
                                  'output_schema': {'type': 'object'}, 'roles': ['member', 'admin'],
                                  'execution': {'mode': 'background', 'confirmation': False},
                                  'presentation': {host: {'renderer': 'workspace'} for host in ('page', 'conversation')}}],
                'surfaces': {'page_tools': [{'id': 'form', 'title': '表单', 'description': '', 'kind': 'form', 'capability_ids': [cap_id]}],
                             'conversation': {'capability_ids': [cap_id], 'starter_prompts': ['开始合成端到端'],
                                              'skills': [{'id': 'start', 'title': '合成端到端', 'description': '先展示表单', 'triggers': ['合成端到端'],
                                                          'entry_capability': cap_id, 'steps': [{'id': 'start', 'title': '填写', 'capability_id': cap_id}]}]}}}
    remote_calls = []

    def provider(request):
        remote_calls.append(request.url.path)
        if request.url.path.endswith('/manifest'):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json={'status': 'ok'})
        pytest.fail('Skill preparation must not invoke a remote business service.')

    class ConfiguredGateway:
        configured = True
        secrets_storage = 'synthetic-test'

        def redact(self, value):
            return value

        async def chat(self, *_args, **_kwargs):
            pytest.fail('Explicit skill command unexpectedly reached the model gateway.')

    manager = RemoteServiceManager(tmp_path / 'services', transport=httpx.MockTransport(provider))
    app = create_app(data_dir=tmp_path / 'real-app', remote_services=manager, gateway=ConfiguredGateway(), start_worker=False)
    with TestClient(app) as client:
        login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'demo-admin-2026'})
        assert login.status_code == 200, login.text
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        connection = client.post('/api/v1/remote-services', json={'name': '合成技能服务', 'base_url': 'https://synthetic-skill.example', 'token': 'synthetic-skill-token'})
        assert connection.status_code == 200, connection.text
        sid = connection.json()['id']
        discovered = client.post(f'/api/v1/remote-services/{sid}/discover')
        assert discovered.status_code == 200, discovered.text
        from registration_helpers import complete_admission
        complete_admission(manager, sid)
        enabled = client.post(f'/api/v1/remote-services/{sid}/enable', json={'expected_revision': discovered.json()['revision']})
        assert enabled.status_code == 200, enabled.text
        cid = client.post('/api/v1/conversations', json={}).json()['id']
        payload = {'content': '/skill synthetic.skill/start', 'idempotency_key': 'explicit-skill'}
        reply = client.post(f'/api/v1/conversations/{cid}/messages', json=payload)
        assert reply.status_code == 200, reply.text
        assert flow(reply.json()['message'])['module_id'] == module_id
        assert forms(reply.json()['message']) == [{'capability_id': cap_id, 'input': {}}]
        replay = client.post(f'/api/v1/conversations/{cid}/messages', json=payload)
        assert replay.status_code == 200 and replay.json()['message']['id'] == reply.json()['message']['id']
        history = client.get(f'/api/v1/conversations/{cid}').json()
        assert any(view['type'] == 'agent-flow' for m in history['messages'] for view in m['views'])
        with app.state.sessions() as db:
            assert db.query(Invocation).count() == 0
        assert not any(path.endswith('/invoke') for path in remote_calls)
