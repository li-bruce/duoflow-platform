"""Content-addressed remote configuration caching without weaker validation."""
import asyncio
from copy import deepcopy
import json
import logging
import os

import httpx
from jsonschema import Draft202012Validator
import pytest

from app.remote_services import RemoteServiceError, RemoteServiceManager
from app.workspace_contract import WORKSPACE_SCHEMA
from registration_helpers import complete_admission


def workspace_manifest(count=3):
    capabilities = [{
        'id': f'example.cache.action{i}', 'version': '1.0.0', 'title': f'合成能力 {i}',
        'description': '缓存测试的通用复合输出', 'kind': 'command',
        'input_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
        'output_schema': deepcopy(WORKSPACE_SCHEMA), 'roles': ['member'],
        'execution': {'mode': 'background', 'confirmation': False},
        'presentation': {'page': {'renderer': 'workspace'}, 'conversation': {'renderer': 'workspace'}},
    } for i in range(count)]
    return {'protocol_version': '1.2', 'module': {'id': 'example.cache', 'version': '1.0.0', 'name': '合成缓存模块', 'description': '隔离测试'},
            'capabilities': capabilities,
            'surfaces': {'page_tools': [{'id': 'form', 'title': '合成表单', 'description': '测试', 'kind': 'form', 'capability_ids': [cap['id'] for cap in capabilities]}],
                         'conversation': {'capability_ids': [cap['id'] for cap in capabilities], 'starter_prompts': []}}}


def configured(tmp_path, *, count=3, token=None):
    def provider(request):
        assert request.url.path == '/platform/v1/health'
        if token:
            assert request.headers['authorization'] == 'Bearer ' + token
        return httpx.Response(200, json={'status': 'ok'})
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(provider))
    manager.save_connection({'id': 'cache-service', 'name': '合成服务', 'base_url': 'https://cache.example', 'token': token or 'synthetic-cache-fixture-token'})
    # Simulate an already-published on-disk connection, as seen after a restart.
    state = manager._read()
    state['services'][0].update(manifest=workspace_manifest(count), enabled=False, discovered_revision=1)
    manager._write(state)
    complete_admission(manager, 'cache-service')
    manager.set_enabled('cache-service', True, 1)
    # Restart after genuine admission; schema counts below concern the new
    # process's first read, rather than the fixture's setup validation.
    return RemoteServiceManager(tmp_path, transport=httpx.MockTransport(provider))


def count_schema_checks(monkeypatch):
    calls = []
    original = Draft202012Validator.check_schema
    def checked(schema):
        calls.append(schema)
        return original(schema)
    monkeypatch.setattr(Draft202012Validator, 'check_schema', staticmethod(checked))
    return calls


def write_exact(path, state, *, stat=None):
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    if stat:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_repeated_registry_reads_validate_large_manifest_only_once(tmp_path, monkeypatch):
    manager = configured(tmp_path, count=20)
    checks = count_schema_checks(monkeypatch)
    baseline = manager.list_services()
    assert len(checks) == 40  # Both schemas of each of the 20 registered capabilities.
    for _ in range(12):
        assert manager.list_services() == baseline
        assert len(manager.enabled_bindings()[0]['manifest']['capabilities']) == 20
        manager.get_binding('cache-service')
        manager.monitor_status('cache-service')
        manager.check_available('cache-service')
    assert len(checks) == 40


def test_health_rewrites_preserve_manifest_validation_cache(tmp_path, monkeypatch):
    manager = configured(tmp_path)
    checks = count_schema_checks(monkeypatch)
    manager.list_services()
    initial = len(checks)
    before = manager.path.read_bytes()
    for _ in range(4):
        result = asyncio.run(manager.health('cache-service'))
        assert result['ok'] is True
        assert manager.list_services()['items'][0]['last_health']['ok'] is True
    assert manager.path.read_bytes() != before
    assert len(checks) == initial == 6


def test_same_size_and_mtime_manifest_tampering_is_revalidated_and_rejected(tmp_path, monkeypatch):
    manager = configured(tmp_path)
    checks = count_schema_checks(monkeypatch)
    original = manager._read()
    baseline = len(checks)
    previous = manager.path.stat()
    tampered = deepcopy(original)
    tampered['services'][0]['manifest']['capabilities'][0]['input_schema']['properties']['text']['type'] = 'foobar'
    write_exact(manager.path, tampered, stat=previous)
    assert manager.path.stat().st_mtime_ns == previous.st_mtime_ns
    assert manager.path.stat().st_size == previous.st_size
    for _ in range(2):
        with pytest.raises(RemoteServiceError) as error:
            manager.list_services()
        assert error.value.code == 'remote_config_corrupt'
    assert len(checks) > baseline
    write_exact(manager.path, original)
    assert manager.list_services()['items'][0]['enabled'] is True


@pytest.mark.parametrize('field,value', [('timeout_seconds', 0), ('last_health', {'ok': 'yes', 'message': 'bad', 'latency_ms': 1}), ('enabled', 'yes')])
def test_cached_manifest_does_not_skip_changed_configuration_field_validation(tmp_path, monkeypatch, field, value):
    manager = configured(tmp_path)
    checks = count_schema_checks(monkeypatch)
    state = manager._read()
    count = len(checks)
    state['services'][0][field] = value
    write_exact(manager.path, state)
    with pytest.raises(RemoteServiceError) as error:
        manager.enabled_bindings()
    assert error.value.code == 'remote_config_corrupt'
    assert len(checks) == count


def test_mutating_first_read_cache_hit_and_public_bindings_never_changes_cached_state(tmp_path):
    manager = configured(tmp_path)
    disk = manager.path.read_bytes()
    first = manager._read()
    first['services'][0]['manifest']['capabilities'][0]['input_schema']['properties']['text']['type'] = 'foobar'
    cached = manager._read()
    cached['services'][0]['manifest']['capabilities'][0]['roles'].append('pretend-root')
    public = manager.list_services()
    public['items'][0]['capabilities'][0]['title'] = 'mutated public value'
    binding = manager.get_binding('cache-service')
    binding['manifest']['surfaces']['conversation']['capability_ids'].clear()
    enabled = manager.enabled_bindings()
    enabled[0]['manifest']['capabilities'].clear()
    pristine = manager.list_services()['items'][0]
    assert pristine['capabilities'][0]['input_schema']['properties']['text']['type'] == 'string'
    assert pristine['capabilities'][0]['roles'] == ['member']
    assert pristine['capabilities'][0]['title'] == '合成能力 0'
    assert len(pristine['manifest']['surfaces']['conversation']['capability_ids']) == 3
    assert manager.path.read_bytes() == disk


def test_new_valid_manifest_content_is_checked_once_even_when_timestamp_is_unchanged(tmp_path, monkeypatch):
    manager = configured(tmp_path)
    checks = count_schema_checks(monkeypatch)
    state = manager._read()
    baseline = len(checks)
    previous = manager.path.stat()
    state['services'][0]['manifest']['capabilities'][0]['input_schema']['properties']['text']['maxLength'] = 123
    # A changed discovery must invalidate its old acceptance, even if its new
    # schemas remain valid. It cannot keep an earlier publication receipt.
    state['services'][0].update(enabled=False, admission=None, registration=None)
    write_exact(manager.path, state, stat=previous)
    assert manager.get_binding('cache-service')['manifest']['capabilities'][0]['input_schema']['properties']['text']['maxLength'] == 123
    assert len(checks) == baseline + 6
    manager.list_services()
    assert len(checks) == baseline + 6


def test_cache_and_validation_errors_do_not_log_credentials(tmp_path, caplog, capsys):
    canary = 'synthetic-cache-secret-not-a-real-credential-2026'
    caplog.set_level(logging.DEBUG)
    manager = configured(tmp_path, token=canary)
    state = manager._read()
    for _ in range(3):
        public = manager.list_services()
        asyncio.run(manager.health('cache-service'))
        assert canary not in json.dumps(public)
        assert 'ciphertext' not in json.dumps(public)
    state['services'][0]['manifest']['capabilities'][0]['input_schema']['properties']['text']['type'] = canary
    write_exact(manager.path, state)
    with pytest.raises(RemoteServiceError) as error:
        manager.list_services()
    captured = capsys.readouterr()
    assert canary not in str(error.value)
    assert canary not in caplog.text + captured.out + captured.err
