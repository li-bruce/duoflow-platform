"""Synthetic protocol fixture for tests of existing transport edge cases.

The temporary peer completes real admission checks through the portable Python
SDK. It never edits acceptance storage or disables production validation. Tests
of bilateral admission and real Node service bindings use their own actual peer.
"""
import asyncio
import importlib.util
from pathlib import Path

import httpx

sdk_path = Path(__file__).resolve().parents[1] / 'templates/module-kit/sdk/python/registration.py'
spec = importlib.util.spec_from_file_location('portable_registration', sdk_path)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


def declaration(manifest):
    return {'registration_version': '1.0', 'module_id': manifest['module']['id'],
            'module_version': manifest['module']['version'],
            'permissions': {'mode': 'capability_roles', 'definition': None},
            'data_objects': [], 'business_chains': [],
            'host_requirements': {'workflows': [], 'interactions': []}, 'workflow_operations': []}


class RegistrationPeer:
    def __init__(self, manifest, token, document=None):
        self.manifest, self.token = manifest, token
        self.document = document or declaration(manifest)
        self.business_calls, self.calls = [], []
        self.confirmation_change = lambda answer: answer
        self.healthy, self.public, self.accept_empty = True, False, False
        self.registrar = sdk.Registration(manifest, self.document,
            {cap['id']: lambda: None for cap in manifest['capabilities']},
            {op: lambda: None for op in self.document['workflow_operations']})

    def __call__(self, request):
        import json
        self.calls.append(request)
        if not self.public and request.headers.get('authorization') != 'Bearer ' + self.token:
            return httpx.Response(401, json={'error': {'code': 'service_auth'}})
        endpoint = request.url.path.split('/platform/v1/')[-1]
        if request.method == 'GET':
            data = {'manifest': self.manifest, 'registration': self.document, 'health': {'ok': self.healthy}}.get(endpoint)
            return httpx.Response(200 if data is not None else 404, json=data)
        body = json.loads(request.content)
        if endpoint == 'handshake':
            try:
                return httpx.Response(200, json=self.confirmation_change(self.registrar.confirm(body)))
            except sdk.RegistrationError:
                return httpx.Response(422, json={'error': {'code': 'registration_contract'}})
        if not body:
            return httpx.Response(200 if self.accept_empty else 422, json={})
        self.business_calls.append(body)
        return httpx.Response(422, json={})


def complete_admission(manager, service_id):
    """Give a focused transport test a fully checked, side-effect-free setup."""
    return asyncio.run(async_complete_admission(manager, service_id))


async def async_complete_admission(manager, service_id):
    item, token, _ = manager._snapshot(service_id)
    previous = manager._transport
    try:
        manager._transport = httpx.MockTransport(RegistrationPeer(item['manifest'], token))
        result = await manager.admit(service_id, item['revision'])
        assert result['admission']['passed'], result['admission']
    finally:
        manager._transport = previous
    return result
