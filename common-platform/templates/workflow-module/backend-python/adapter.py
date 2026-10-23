"""Neutral installed Python authority: private notes, no files/jobs or model calls.

Command handlers use the platform invocation transaction and its durable
idempotency receipt. Adapter reads query exactly the same module-owned table.
Do not call commit here or duplicate these objects in a remote service.
"""
import base64
import hashlib
import json

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select, update

from app.contracts import CapabilityOutput
from app.db import audit, now, uid
from app.errors import require
from app.identity import has_role
from app.job_contract import validate_page_input


OBJECT_TYPE = 'example.note'
metadata = MetaData()
notes = Table('example_workflow_notes', metadata,
    Column('id', String(40), primary_key=True), Column('owner_id', String(40), nullable=False, index=True),
    Column('revision', Integer, nullable=False), Column('state', String(30), nullable=False),
    Column('title', String(200), nullable=False), Column('body', Text, nullable=False),
    Column('created_at', String(50), nullable=False), Column('updated_at', String(50), nullable=False))


def authorized(actor):
    require(has_role(actor, 'member'), 'forbidden', '当前身份不能使用此业务对象。', 403)


def load(db, actor, identifier):
    authorized(actor)
    item = db.execute(select(notes).where(notes.c.id == identifier)).mappings().one_or_none()
    require(item is not None and item['owner_id'] == actor['id'], 'object_scope', '对象不存在或当前身份不可见。', 404)
    return item


def projection(item):
    return {'object_ref': {'object_type': OBJECT_TYPE, 'object_id': item['id']},
        'revision': item['revision'], 'state': item['state'],
        'data': {'title': item['title'], 'body': item['body']},
        'allowed_actions': ['edit'] if item['state'] == 'draft' else [], 'jobs': [], 'updated_at': item['updated_at']}


def listing(db, actor, payload=None):
    authorized(actor)
    payload = payload or {}
    validate_page_input('objects.list', payload)
    require(payload.get('object_type', OBJECT_TYPE) == OBJECT_TYPE, 'object_type', '对象类型不匹配。', 422)
    # Bind the opaque cursor to this private list and its complete revision
    # snapshot. Only small identifiers/revisions are read for the fingerprint;
    # bodies are fetched for the requested page. A cursor grants no authority.
    revisions = list(db.execute(select(notes.c.id, notes.c.revision).where(notes.c.owner_id == actor['id']).order_by(notes.c.id)))
    snapshot = hashlib.sha256(json.dumps([list(row) for row in revisions]).encode()).hexdigest()
    binding = {'owner': actor['id'], 'authorization_revision': actor.get('authorization_revision', 1),
               'object_type': OBJECT_TYPE, 'snapshot': snapshot}
    offset, limit = 0, payload.get('limit', 20)
    if payload.get('cursor'):
        try:
            raw = payload['cursor']
            cursor = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
        except (ValueError, UnicodeError):
            cursor = None
        require(isinstance(cursor, dict) and set(cursor) == {*binding, 'offset'}
                and type(cursor['offset']) is int and cursor['offset'] >= 0,
                'cursor_invalid', '分页游标无效。', 422)
        require(all(cursor[key] == value for key, value in binding.items()), 'revision_conflict', '记录列表已变化，请重新读取。', 409)
        require(cursor['offset'] <= len(revisions), 'cursor_invalid', '分页游标无效。', 422)
        offset = cursor['offset']
    values = list(db.execute(select(notes).where(notes.c.owner_id == actor['id']).order_by(notes.c.updated_at.desc(), notes.c.id).offset(offset).limit(limit)).mappings())
    latest = list(db.execute(select(notes.c.id, notes.c.revision).where(notes.c.owner_id == actor['id']).order_by(notes.c.id)))
    require(latest == revisions, 'revision_conflict', '读取期间记录列表已经变化，请重试。', 409)
    next_cursor = None
    if offset + len(values) < len(revisions):
        next_cursor = base64.urlsafe_b64encode(json.dumps({**binding, 'offset': offset + len(values)}, separators=(',', ':')).encode()).decode().rstrip('=')
    # A directory page does not repeat every object's body. Read the selected
    # record for its full projection; this keeps even 50 entries below 128 KiB.
    return {'objects': [{**projection(item), 'data': {'title': item['title']}} for item in values], 'next_cursor': next_cursor}


def create_note(ctx, payload):
    authorized(ctx.actor)
    identifier, stamp = uid(), now()
    command = payload['command']
    ctx.session.execute(insert(notes).values(id=identifier, owner_id=ctx.actor['id'], revision=1, state='draft',
        title=command['title'], body=command['body'], created_at=stamp, updated_at=stamp))
    audit(ctx.session, ctx.actor['id'], 'example.note.create', identifier, revision=1)
    return CapabilityOutput('已创建记录。', {'projection': projection(load(ctx.session, ctx.actor, identifier))})


def edit_note(ctx, payload):
    item = load(ctx.session, ctx.actor, payload['object_id'])
    require(item['state'] == 'draft', 'state_conflict', '当前对象不允许编辑。', 409)
    require(item['revision'] == payload['expected_revision'], 'revision_conflict', '对象版本已经变化，请重新读取。', 409)
    command, revision = payload['command'], item['revision'] + 1
    changed = ctx.session.execute(update(notes).where(notes.c.id == item['id'], notes.c.owner_id == ctx.actor['id'],
        notes.c.revision == payload['expected_revision'], notes.c.state == 'draft').values(
        title=command['title'], body=command['body'], revision=revision, updated_at=now()))
    require(changed.rowcount == 1, 'revision_conflict', '另一个请求已经修改该对象。', 409)
    audit(ctx.session, ctx.actor['id'], 'example.note.edit', item['id'], revision=revision)
    return CapabilityOutput('已保存记录。', {'projection': projection(load(ctx.session, ctx.actor, item['id']))})


def read_note(ctx, payload):
    return CapabilityOutput('已读取当前记录。', {'projection': projection(load(ctx.session, ctx.actor, payload['object_id']))})


def list_notes(ctx, payload):
    return CapabilityOutput('已读取本人记录。', listing(ctx.session, ctx.actor))


class Adapter:
    def __init__(self, context):
        self.sessions = context.sessions
        # Schema creation is installation/startup work, not a second object store.
        with self.sessions() as db:
            metadata.create_all(db.get_bind())

    def request(self, *, actor, capability_id, operation, payload, request_id):
        require(operation in {'objects.list', 'objects.read'}, 'workflow_adapter_missing', '此模块未实现文件或长任务适配。', 501)
        with self.sessions() as db:
            if operation == 'objects.list':
                return listing(db, actor, payload)
            ref = payload['object_ref']
            require(ref['object_type'] == OBJECT_TYPE, 'object_type', '对象类型不匹配。', 422)
            return {'projection': projection(load(db, actor, ref['object_id']))}


def create(context):
    return Adapter(context)
