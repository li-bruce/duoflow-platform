"""Source-bound checks for composition foundations and current limitations.

Two synthetic modules run only against the isolated framework-lab fixture.
These checks do not introduce a composition executor or installed business pack.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from app.contracts import CapabilityOutput
from app.errors import PlatformError
from app.module_contract import normalize_surfaces
from app.workspace_contract import normalize_skills
from test_framework_lab import host

ROOT = Path(__file__).resolve().parents[1]


def register_second_module(host, roles=None):
    calls = []
    module = {'id': 'sample.receiver', 'version': '1.0.0', 'name': '合成接收模块', 'description': '仅测试'}
    spec = {'id': 'sample.receiver.read', 'version': '1.0.0', 'title': '接收明确引用',
            'description': '显式输入，不自动理解字段含义。', 'kind': 'query', 'roles': roles or ['member'],
            'execution': {'mode': 'sync', 'confirmation': False},
            'presentation': {surface: {'renderer': 'json'} for surface in ('page', 'conversation')},
            'input_schema': {'type': 'object', 'properties': {'reference': {'type': 'string'}},
                             'required': ['reference'], 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': {'reference': {'type': 'string'}},
                              'required': ['reference'], 'additionalProperties': False}}
    module['surfaces'] = normalize_surfaces(module['id'], [spec])
    host.runtime.registry.modules.append(module)
    def handle(ctx, payload):
        calls.append({'actor_id': ctx.actor['id'], 'input': deepcopy(payload)})
        return CapabilityOutput('已接收明确引用。', deepcopy(payload))
    host.runtime.registry.register(module, spec, handle)
    return calls


def test_global_catalog_and_result_action_can_reference_another_module_without_auto_execution(host):
    calls = register_second_module(host)
    actor = host.actors['alice']
    catalog = host.runtime.public_registry(actor)
    assert {'framework-lab', 'sample.receiver'} <= {item['id'] for item in catalog['modules']}
    workspace = {'blocks': [{'type': 'actions', 'items': [
        {'label': '交给接收模块', 'capability_id': 'sample.receiver.read',
         'input': {'reference': 'synthetic-source-1', 'not_a_target_field': 'discard'}}]}]}
    prepared = host.runtime.refresh_workspace(workspace, actor, 'framework-lab.read')
    action = prepared['blocks'][0]['items'][0]
    assert action['input'] == {'reference': 'synthetic-source-1'}
    assert calls == []  # Preparing a later action does not execute it.
    result = asyncio.run(host.runtime.invoke(actor, action['capability_id'], action['input'], 'cross-module-explicit', 'page'))
    assert result['data'] == {'reference': 'synthetic-source-1'}
    assert calls == [{'actor_id': actor['id'], 'input': {'reference': 'synthetic-source-1'}}]


def test_target_permission_is_checked_independently_of_the_source_module(host):
    calls = register_second_module(host, ['admin'])
    actor = host.actors['alice']
    source = {'blocks': [{'type': 'actions', 'items': [
        {'label': '交给接收模块', 'capability_id': 'sample.receiver.read', 'input': {'reference': 'source-1'}}]}]}
    assert host.runtime.refresh_workspace(source, actor, 'framework-lab.read')['blocks'][0]['items'] == []
    with pytest.raises(PlatformError) as caught:
        asyncio.run(host.runtime.invoke(actor, 'sample.receiver.read', {'reference': 'source-1'}, 'not-authorized', 'page'))
    assert caught.value.code == 'forbidden' and not calls


def test_skill_steps_cannot_reference_capabilities_outside_the_declaring_module():
    own = [{'id': 'sample.source.create'}]
    skill = {'id': 'chain', 'title': '合成串联', 'description': '', 'triggers': ['合成串联'],
             'entry_capability': 'sample.source.create',
             'steps': [{'id': 'first', 'title': '第一步', 'capability_id': 'sample.source.create'},
                       {'id': 'second', 'title': '第二步', 'capability_id': 'sample.receiver.read'}]}
    with pytest.raises(PlatformError) as caught:
        normalize_skills([skill], own)
    assert caught.value.code == 'skill_contract'


@pytest.mark.parametrize('addition', ['providers', 'input_bindings'])
def test_current_workflow_schema_does_not_accept_multi_provider_or_data_binding_fields(addition):
    definition = json.loads((ROOT / 'flows/packs/framework-lab/flow.json').read_text(encoding='utf-8'))
    if addition == 'providers':
        definition['flows'][0]['providers'] = {'source': {}, 'receiver': {}}
    else:
        definition['flows'][0]['steps'][0]['actions'][0]['input_bindings'] = {'reference': 'steps.source.output.id'}
    schema = json.loads((ROOT / 'contracts/workflows/v1/definition.schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(definition))
    assert any(error.validator == 'additionalProperties' and addition in error.message for error in errors)


def test_missing_or_incompatible_source_value_is_not_automatically_transformed(host):
    calls = register_second_module(host)
    with pytest.raises(PlatformError) as caught:
        asyncio.run(host.runtime.invoke(host.actors['alice'], 'sample.receiver.read', {'reference': {'id': 'source-1'}}, 'wrong-type', 'conversation'))
    assert caught.value.code == 'schema_validation' and not calls
