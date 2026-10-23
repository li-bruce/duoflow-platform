"""Portable debug acceptance: only the neutral module and generic roles."""
from copy import deepcopy
import json

from fastapi import Header
import pytest

from app.db import WorkflowInstance, WorkflowObservation
from app.devtools import Devtools, declaration
from app.devtools_routes import create_devtools_router
from test_framework_lab import FLOW_ID, act, host, open_flow


BASE = '/api/v1/devtools'
PRIVATE_BODY = '内部输入正文仅在正式组件内显示，不进入调试元数据。'


@pytest.fixture
def debug(host):
    def actor(x_actor: str = Header(default='system')):
        return host.actors[x_actor]
    host.devtools = Devtools(host.runtime)
    host.client.app.include_router(create_devtools_router(host.devtools, actor, actor))
    return host


def read(debug, item, who='system'):
    return debug.client.get(BASE + '/instances/' + item['id'], headers={'X-Actor': who})


def record(debug):
    return act(debug, open_flow(debug, who='system'), 'create',
               {'title': '通用验收记录', 'body': PRIVATE_BODY}, who='system')


def test_portable_catalog_binds_neutral_frontend_backend_and_machine(debug):
    response = debug.client.get(BASE + '/catalog')
    assert response.status_code == 200, response.text
    catalog = response.json()
    flow = next(item for item in catalog['workflows'] if item['id'] == FLOW_ID)
    assert flow['module_ids'] == ['framework-lab']
    assert flow['registration'] == {'declaration_loaded': True, 'pack_installed': True, 'source_present': True,
                                    'backend_available': True, 'state_machine_declared': True}
    assert flow['definition']['state_machine'] == debug.module.MACHINE
    backend = next(item for item in catalog['capabilities'] if item['id'] == 'framework-lab.submit')
    assert FLOW_ID in backend['bindings']['workflows']


def test_portable_live_state_follows_real_module_and_redacts_payload(debug):
    item = record(debug)
    result = read(debug, item)
    assert result.status_code == 200, result.text
    first = result.json()
    assert first['business']['state'] == 'draft' and first['cursor'] == 'edit'
    assert first['business']['known_state'] is True
    assert read(debug, item).json()['history'] == first['history']
    states = ['draft']
    for action, value in [('submit', {}), ('return', {'note': '请补充'}), ('submit', {}), ('complete', {'note': '已验证'})]:
        item = act(debug, item, action, value, who='system')
        result = read(debug, item)
        assert result.status_code == 200, result.text
        states.append(result.json()['business']['state'])
    assert states == ['draft', 'submitted', 'returned', 'submitted', 'completed']
    assert [row['business_state'] for row in result.json()['history']] == states
    assert result.json()['business']['allowed_actions'] == []
    assert PRIVATE_BODY not in result.text
    assert 'drafts' not in result.json() and 'projection' not in result.json()
    with debug.sessions() as db:
        encoded = json.dumps([entry.observed for entry in db.query(WorkflowObservation)], ensure_ascii=False)
        assert PRIVATE_BODY not in encoded and '请补充' not in encoded


def test_portable_admin_has_no_other_owners_debug_instance(debug):
    item = record(debug)
    assert read(debug, item, 'legacy').status_code == 403
    assert debug.client.get(BASE + '/instances', headers={'X-Actor': 'legacy'}).json()['items'] == []
    assert debug.client.get(BASE + '/instances').json()['items'][0]['id'] == item['id']


def test_portable_member_cannot_access_management_debug_even_for_own_flow(debug):
    item = open_flow(debug, who='alice')
    assert read(debug, item, 'alice').status_code == 403
    assert debug.client.get(BASE + '/catalog', headers={'X-Actor': 'alice'}).status_code == 403
    response = debug.client.post(BASE + '/validate', json={'kind': 'state_machine', 'definition': debug.module.MACHINE}, headers={'X-Actor': 'alice'})
    assert response.status_code == 403


def test_portable_declaration_replacement_preview_has_no_mutations(debug):
    before = deepcopy(debug.runtime.workflows.definitions)
    value = declaration(before[FLOW_ID]); value['title'] = '仅预览更改'
    response = debug.client.post(BASE + '/validate', json={'kind': 'workflow', 'definition': value})
    assert response.status_code == 200 and response.json()['valid'], response.text
    assert response.json()['replacement']['registered'] and response.json()['replacement']['changed']
    assert debug.runtime.workflows.definitions == before
    with debug.sessions() as db:
        assert db.query(WorkflowInstance).count() == db.query(WorkflowObservation).count() == 0
    value['state_machine']['transitions'][0]['action_id'] = 'undeclared'
    assert not debug.client.post(BASE + '/validate', json={'kind': 'workflow', 'definition': value}).json()['valid']


def test_portable_unresolvable_local_schema_is_diagnostic_not_server_error(debug):
    value = {'protocol_version': '1.0', 'id': 'example.component', 'version': '1.0.0', 'title': 'Schema 验证', 'description': '缺少引用',
             'input_schema': {'type': 'object'}, 'state_schema': {'type': 'object', '$ref': '#/missing'}, 'initial_state': {},
             'actions': [{'id': 'submit', 'title': '提交', 'capability_id': 'framework-lab.submit', 'capability_version': '1.0.0', 'bindings': {}, 'completes': False}]}
    response = debug.client.post(BASE + '/validate', json={'kind': 'interaction', 'definition': value})
    assert response.status_code == 200 and not response.json()['valid'], response.text
    assert response.json()['diagnostics'][0]['code'] == 'devtools_schema'
