from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app.access_control import AccessAudit, AccessControl, AccessGrant, CasbinDecisionEngine, validate_definition
from app.db import create_database
from app.errors import PlatformError


def definition(module='example-intake'):
    permission = lambda name: f'{module}.{name}'
    return {
        'contract_version': '1.0', 'module_id': module, 'version': '1.0.0', 'title': '合成业务模块',
        'scope_keys': ['project_id', 'organization_id', 'owner_id', 'workspace_id'],
        'permissions': [
            {'id': permission('read'), 'title': '读取'},
            {'id': permission('edit'), 'title': '修改', 'guards': [{'type': 'actor_is_fact', 'fact': 'owner_id'}]},
            {'id': permission('review'), 'title': '审核', 'guards': [{'type': 'actor_not_fact', 'fact': 'owner_id'}, {'type': 'fact_in', 'fact': 'state', 'values': ['submitted']}]},
        ],
        'role_templates': [
            {'id': permission('submitter'), 'title': '提交者', 'permissions': [permission('read'), permission('edit')], 'required_scope': ['project_id', 'owner_id']},
            {'id': permission('reviewer'), 'title': '审核者', 'permissions': [permission('read'), permission('review')], 'required_scope': ['project_id', 'organization_id']},
        ],
        'command_mappings': [
            {'capability_id': permission('read'), 'permission': permission('read')},
            {'capability_id': permission('command'), 'command': 'edit', 'permission': permission('edit')},
            {'capability_id': permission('command'), 'command': 'review', 'permission': permission('review')},
        ],
        'experiences': [
            {'id': permission('author-view'), 'title': '提交办理', 'description': '填写材料', 'flow_id': 'example.submit', 'required_permissions': [permission('read'), permission('edit')], 'step_ids': ['material', 'submit']},
            {'id': permission('review-view'), 'title': '审核办理', 'description': '核验材料', 'flow_id': 'example.review', 'required_permissions': [permission('read'), permission('review')]},
        ],
        'workitem_sources': [{'id': permission('pending'), 'capability_id': permission('read'), 'experience_id': permission('review-view')}],
    }


@pytest.fixture
def access(tmp_path):
    engine, sessions = create_database(tmp_path)
    clock = [1_800_000_000.0]
    service = AccessControl(sessions, clock=lambda: clock[0])
    service.register_definition(definition(), actor_id='manager')
    yield service, sessions, clock
    engine.dispose()


def grant(service, subject='alice', role='submitter', scope=None, module='example-intake', **extra):
    if scope is None:
        scope = {'project_id': 'P', 'owner_id': subject} if role == 'submitter' else {'project_id': 'P', 'organization_id': 'receiving-org'}
    return service.grant(subject, module, f'{module}.{role}', scope, actor_id='manager', **extra)


def facts(**extra):
    return dict(project_id='P', organization_id='receiving-org', owner_id='alice', state='submitted', **extra)


def test_real_casbin_default_deny_and_registration_never_grants(access):
    service, sessions, _ = access
    assert isinstance(service.engine, CasbinDecisionEngine)
    assert service.module_registered('example-intake')
    assert not service.has_permission({'id': 'manager', 'role': 'system_admin'}, 'example-intake.read')
    assert not service.check({'id': 'alice'}, 'example-intake.read', facts())['allowed']
    assert service.bindings() == []
    initial = service.policy_revision('example-intake')
    service.register_definition(definition(), actor_id='manager')
    assert service.policy_revision('example-intake') == initial
    with sessions() as db:
        assert len(db.scalars(select(AccessAudit)).all()) == 1


def test_scoped_subject_object_and_cross_module_authorization(access):
    service, _, _ = access
    binding = grant(service)
    actor = {'id': 'alice'}
    result = service.check(actor, 'example-intake.edit', facts())
    assert result['allowed'] and result['grant_ids'] == [binding['id']]
    assert result['engine'] == 'casbin-1.43.0'
    assert service.has_permission(actor, 'example-intake.read')
    for other in ({**facts(), 'project_id': 'Q'}, {**facts(), 'owner_id': 'bob'}, {}):
        assert not service.check(actor, 'example-intake.read', other)['allowed']
    assert not service.check({'id': 'bob'}, 'example-intake.read', facts())['allowed']
    assert not service.check({'id': 'alice', 'enabled': False}, 'example-intake.read', facts())['allowed']
    service.register_definition(definition('example-review'), actor_id='manager')
    assert not service.check(actor, 'example-review.read', facts())['allowed']
    assert service.matching_scopes(actor, 'example-intake.read') == [{'project_id': 'P', 'owner_id': 'alice'}]


def test_scope_conjunction_never_combines_separate_grants(access):
    service, _, _ = access
    grant(service, role='reviewer', scope={'project_id': 'P', 'organization_id': 'A'})
    grant(service, role='reviewer', scope={'project_id': 'Q', 'organization_id': 'B'})
    actor = {'id': 'alice'}
    actual = {**facts(), 'owner_id': 'other', 'project_id': 'P', 'organization_id': 'B'}
    assert service.has_permission(actor, 'example-intake.review')
    assert not service.check(actor, 'example-intake.review', actual)['allowed']
    actual['organization_id'] = ['receiving-org', 'A']
    assert service.check(actor, 'example-intake.review', actual)['allowed']
    actual['organization_id'] = ['A', 123]
    assert not service.check(actor, 'example-intake.review', actual)['allowed']


def test_explicit_duties_and_business_guards_override_two_roles(access):
    service, _, _ = access
    grant(service)
    grant(service, role='reviewer')
    actor = {'id': 'alice'}
    assert service.check(actor, 'example-intake.review', facts())['reason'] == 'separation_of_duties'
    other = {**facts(), 'owner_id': 'other'}
    assert service.check(actor, 'example-intake.review', other)['allowed']
    assert service.check(actor, 'example-intake.review', {**other, 'state': 'draft'})['reason'] == 'fact_condition_failed'
    del other['owner_id']
    assert service.check(actor, 'example-intake.review', other)['reason'] == 'required_fact_missing'


def test_expiry_revocation_and_revision_invalidate_resume_stamps(access):
    service, _, clock = access
    actor = {'id': 'alice'}
    before = service.authorization_stamp(actor)
    binding = grant(service, expires_at=clock[0] + 5)
    stamp = service.authorization_stamp(actor)
    assert stamp != before and stamp == service.authorization_stamp(actor)
    revision = service.policy_revision('example-intake')
    assert service.check(actor, 'example-intake.read', facts(), expected_policy_revision=revision)['allowed']
    clock[0] += 5
    assert not service.has_permission(actor, 'example-intake.read')
    assert service.authorization_stamp(actor) != stamp
    service.revoke(binding['id'], actor_id='manager')
    assert service.policy_revision('example-intake') == revision + 1
    service.revoke(binding['id'], actor_id='manager')
    assert service.policy_revision('example-intake') == revision + 1
    assert service.check(actor, 'example-intake.read', facts(), expected_policy_revision=revision)['reason'] == 'policy_revision_changed'


def test_upgrade_new_permission_does_not_expand_old_grant(access):
    service, _, _ = access
    binding = grant(service)
    updated = definition()
    updated['version'] = '1.1.0'
    updated['role_templates'][0]['permissions'].append('example-intake.review')
    service.register_definition(updated, actor_id='manager')
    assert not service.has_permission({'id': 'alice'}, 'example-intake.review')
    assert service.bindings('alice')[0]['definition_revision'] == binding['definition_revision']
    grant(service)
    assert service.has_permission({'id': 'alice'}, 'example-intake.review')
    updated['version'] = '1.2.0'
    updated['role_templates'][0]['permissions'] = ['example-intake.edit']
    service.register_definition(updated, actor_id='manager')
    assert not service.has_permission({'id': 'alice'}, 'example-intake.read')


def test_added_required_scope_closes_existing_incomplete_binding(access):
    service, _, _ = access
    grant(service)
    updated = definition()
    updated['version'] = '2.0'
    updated['role_templates'][0]['required_scope'].append('workspace_id')
    service.register_definition(updated, actor_id='manager')
    assert not service.has_permission({'id': 'alice'}, 'example-intake.read')


def test_experiences_commands_and_unknown_command_do_not_fallback(access):
    service, _, _ = access
    grant(service)
    actor = {'id': 'alice'}
    assert [x['id'] for x in service.experiences(actor)] == ['example-intake.author-view']
    assert service.experiences(actor, facts={**facts(), 'project_id': 'Q'}) == []
    assert service.experiences(actor)[0]['step_ids'] == ['material', 'submit']
    assert service.capability_permissions('example-intake.command') == ['example-intake.edit', 'example-intake.review']
    assert service.check_command(actor, 'example-intake.command', {'command': 'edit'}, facts())['allowed']
    assert not service.check_command(actor, 'example-intake.command', {'command': 'review'}, facts())['allowed']
    assert service.check_command(actor, 'example-intake.command', {}, facts())['reason'] == 'command_unmapped'
    assert service.check_command(actor, 'example-intake.read', {'command': 'destroy'}, facts())['reason'] == 'command_unmapped'
    assert service.check_command(actor, 'example-intake.read', {'command': {'action_id': 'read'}}, facts())['allowed']
    assert not service.check_command(actor, 'example-intake.command', {'command': {'action_id': 'edit'}}, facts())['allowed']


@pytest.mark.parametrize('scope', [{}, {'project_id': '*', 'owner_id': 'alice'}, {'project_id': 'P'}, {'project_id': 'P', 'owner_id': 'alice', 'invented': 'x'}, {'project_id': ['P'], 'owner_id': 'alice'}])
def test_invalid_bindings_fail_closed(access, scope):
    service, _, _ = access
    with pytest.raises(PlatformError):
        grant(service, scope=scope)


@pytest.mark.parametrize('mutation', [
    lambda d: d['permissions'][0].update(id='other.read'),
    lambda d: d['role_templates'][0]['permissions'].append('example-intake.unknown'),
    lambda d: d['role_templates'][0]['required_scope'].append('invented'),
    lambda d: d['command_mappings'].append({'capability_id': 'example-intake.command', 'permission': 'example-intake.read'}),
    lambda d: d['command_mappings'].append(deepcopy(d['command_mappings'][0])),
    lambda d: d['experiences'][0]['required_permissions'].append('other.read'),
    lambda d: d['permissions'][0].update(guards=[{'type': 'eval', 'expression': 'True'}]),
    lambda d: d.update(grants=[{'subject_id': 'attacker'}]),
])
def test_namespace_references_and_non_executable_contract(mutation):
    value = definition()
    mutation(value)
    with pytest.raises(PlatformError):
        validate_definition(value)


def test_persistence_disabled_module_and_stale_upgrade(access):
    service, sessions, _ = access
    grant(service)
    copy = AccessControl(sessions, clock=service.clock)
    assert copy.check({'id': 'alice'}, 'example-intake.read', facts())['allowed']
    version = service.policy_revision('example-intake')
    service.set_enabled('example-intake', False, actor_id='manager')
    assert service.module_registered('example-intake')
    assert not copy.has_permission({'id': 'alice'}, 'example-intake.read')
    assert copy.experiences({'id': 'alice'}) == []
    updated = definition()
    updated['version'] = '2.0'
    with pytest.raises(PlatformError, match='权限策略已更新'):
        service.register_definition(updated, actor_id='manager', expected_revision=version)
    with pytest.raises(PlatformError):
        service.register_definition({**definition(), 'title': 'changed'}, actor_id='manager')


def test_decisions_do_not_write_audit_or_commit_borrowed_transactions(access):
    service, sessions, _ = access
    binding = grant(service)
    with sessions() as db:
        before = len(db.scalars(select(AccessAudit)).all())
        borrowed = AccessControl.from_session(db, clock=service.clock)
        row = db.get(AccessGrant, binding['id'])
        row.scope = {'project_id': 'Q', 'owner_id': 'alice'}
        db.flush()
        assert not borrowed.check({'id': 'alice'}, 'example-intake.read', facts())['allowed']
        assert len(db.scalars(select(AccessAudit)).all()) == before
        with pytest.raises(PlatformError, match='仅可读取'):
            grant(borrowed)
        db.rollback()
    assert service.check({'id': 'alice'}, 'example-intake.read', facts())['allowed']


def test_other_subject_authorization_changes_do_not_break_resume(access):
    service, _, _ = access
    grant(service)
    actor = {'id': 'alice'}
    stamp = service.authorization_stamp(actor)
    bobs_grant = grant(service, subject='bob')
    assert service.authorization_stamp(actor) == stamp
    service.revoke(bobs_grant['id'], actor_id='manager')
    assert service.authorization_stamp(actor) == stamp
    updated = definition()
    updated['version'] = '2.0'
    updated['permissions'][0]['guards'] = [{'type': 'actor_is_fact', 'fact': 'owner_id'}]
    service.register_definition(updated, actor_id='manager')
    assert service.authorization_stamp(actor) != stamp


def test_nested_module_namespace_cannot_capture_existing_permissions(access):
    service, _, _ = access
    with pytest.raises(PlatformError, match='相互包含'):
        service.register_definition(definition('example-intake.child'), actor_id='manager')


def test_disable_enable_cycle_does_not_resurrect_old_flow_stamp(access):
    service, _, _ = access
    grant(service)
    actor = {'id': 'alice'}
    previous = service.authorization_stamp(actor)
    service.set_enabled('example-intake', False, actor_id='manager')
    service.set_enabled('example-intake', True, actor_id='manager')
    assert service.check(actor, 'example-intake.read', facts())['allowed']
    assert service.authorization_stamp(actor) != previous
