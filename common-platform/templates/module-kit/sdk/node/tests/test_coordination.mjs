import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { DatabaseSync } from 'node:sqlite';
import { CoordinationClient, CoordinationError, initializeCoordination, enqueue, validateEvent } from '../coordination.mjs';

const SCOPE = { organization_id: 'example-org', project_id: 'example-project' };
const TOKEN = 'synthetic-module-token-for-coordination-only';
const event = (number = 1, objectId = 'sample') => ({ version: '1.0', event_id: 'event-' + number, module_id: 'source', kind: 'state.changed', machine_id: 'resource', object_ref: { object_type: 'resource', object_id: objectId }, state: 'pending', revision: number, scope: { ...SCOPE } });
const delivery = () => ({ id: 'delivery-1', subscription_id: 'subscription-1', target_module: 'target', mode: 'notify', event: event(), consumer_id: 'worker-1', lease_token: 'synthetic-lease-token-first', lease_until: 9999999999, attempts: 1 });
const client = (changes = {}) => new CoordinationClient({ baseUrl: 'http://127.0.0.1:19090/api/v1/coordination', token: TOKEN, moduleId: 'source', consumerId: 'worker-1', scopes: [SCOPE], acceptedSources: ['source'], ...changes });
function database(filename) {
  const db = new DatabaseSync(filename);
  initializeCoordination(db);
  db.exec('CREATE TABLE IF NOT EXISTS business(id TEXT PRIMARY KEY,state TEXT NOT NULL,applications INTEGER NOT NULL DEFAULT 0)');
  return db;
}
function fixture(t) {
  // Explicit workspace path; no system temp data or existing runtime is touched.
  const parent = path.resolve(process.env.MODULE_COORD_TEST_DIR || 'work/coordination-node-tests');
  fs.mkdirSync(parent, { recursive: true });
  const dir = fs.mkdtempSync(path.join(parent, 'module-'));
  t.after(() => { assert.equal(path.dirname(dir), parent); fs.rmSync(dir, { recursive: true, force: true }); });
  return path.join(dir, 'module.sqlite3');
}
function transaction(db, operation) {
  db.exec('BEGIN IMMEDIATE');
  try { const result = operation(); db.exec('COMMIT'); return result; }
  catch (error) { db.exec('ROLLBACK'); throw error; }
}

test('business + outbox rollback, durable retry, restart, bounded per-object ordering', async t => {
  const file = fixture(t); let db = database(file), now = 100;
  let c = client({ clock: () => now, fetchImpl: async () => new Response('', { status: 503 }) });
  assert.throws(() => transaction(db, () => { db.exec("INSERT INTO business VALUES ('sample','pending',0)"); c.enqueue(db, event()); throw Error('rollback'); }));
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM business').get().n, 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM duoflow_coord_outbox').get().n, 0);
  transaction(db, () => {
    db.exec("INSERT INTO business VALUES ('sample','pending',0)");
    c.enqueue(db, event()); c.enqueue(db, event(2)); c.enqueue(db, event(3, 'other-object'));
    assert.equal(c.enqueue(db, event()), false);
    assert.throws(() => c.enqueue(db, { ...event(), state: 'changed' }), /coordination_event_conflict/);
  });
  assert.deepEqual(await c.relay(db), { sent: 0, failed: 2 });
  assert.deepEqual(await c.relay(db), { sent: 0, failed: 0 });
  db.close(); db = database(file); now += 2;
  const received = [];
  c = client({ clock: () => now, fetchImpl: async (_, options) => {
    assert.equal(options.headers.Authorization, 'Bearer ' + TOKEN);
    assert.equal(options.body.includes(TOKEN), false);
    const body = JSON.parse(options.body); received.push(body.event_id);
    return Response.json({ accepted: true, duplicate: false, event_id: body.event_id });
  } });
  assert.deepEqual(await c.relay(db), { sent: 2, failed: 0 });
  assert.deepEqual(await c.relay(db), { sent: 1, failed: 0 });
  assert.deepEqual(received, ['event-1', 'event-3', 'event-2']);
  db.close();
});

test('handler rollback, commit before ACK, ACK loss and restart do not repeat business', async t => {
  const file = fixture(t); let db = database(file);
  db.exec("INSERT INTO business VALUES ('sample','pending',0)");
  const receipts = [];
  const c = client({ moduleId: 'target', fetchImpl: async (_, options) => {
    assert.equal(db.isTransaction, false);
    receipts.push(JSON.parse(options.body));
    if (receipts.length === 1) throw Error('PRIVATE transport content');
    return Response.json({ acknowledged: true, duplicate: false, delivery_id: 'delivery-1' });
  } });
  await assert.rejects(c.consume(db, delivery(), conn => { conn.exec('UPDATE business SET applications=applications+1'); throw Error('business validation failed'); }));
  assert.equal(receipts.length, 0);
  assert.equal(db.prepare('SELECT applications FROM business').get().applications, 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM duoflow_coord_inbox').get().n, 0);
  const apply = conn => conn.exec('UPDATE business SET applications=applications+1');
  assert.deepEqual(await c.consume(db, delivery(), apply), { applied: true, acknowledged: false, error: 'coordination_transport' });
  db.close(); db = database(file);
  const repeated = { ...delivery(), lease_token: 'synthetic-lease-token-second', attempts: 2 };
  assert.deepEqual(await c.consume(db, repeated, apply), { applied: false, acknowledged: true });
  assert.equal(receipts.at(-1).lease_token, repeated.lease_token);
  assert.equal(db.prepare('SELECT applications FROM business').get().applications, 1);
  await assert.rejects(c.consume(db, { ...repeated, event: { ...event(), state: 'changed' } }, apply), /coordination_delivery_conflict/);
  db.close();
});

test('center accepted event but lost response: stable event ID is retried after restart', async t => {
  const file = fixture(t); let db = database(file), calls = 0, now = 100;
  const c = client({ clock: () => now, fetchImpl: async (_, options) => {
    const body = JSON.parse(options.body); calls++;
    assert.equal(body.event_id, 'event-1');
    if (calls === 1) throw Error('connection lost after center commit');
    return Response.json({ accepted: true, duplicate: true, event_id: body.event_id });
  } });
  transaction(db, () => c.enqueue(db, event()));
  assert.deepEqual(await c.relay(db), { sent: 0, failed: 1 });
  db.close(); db = database(file); now += 2;
  assert.deepEqual(await c.relay(db), { sent: 1, failed: 0 });
  assert.equal(calls, 2); db.close();
});

test('receipt, source/target/scope checks, strict envelope and active transaction', async t => {
  const item = delivery();
  const c = client({ moduleId: 'target', fetchImpl: async () => Response.json({ version: '1.0', module_id: 'target', consumer_id: 'worker-1', deliveries: [item] }) });
  assert.deepEqual(await c.pull(), [item]);
  for (const patch of [{ target_module: 'elsewhere' }, { consumer_id: 'elsewhere' }, { lease_token: 'short' }, { event: { ...event(), scope: { ...SCOPE, project_id: 'other' } } }, { event: { ...event(), module_id: 'forged' } }])
    assert.throws(() => c.validateDelivery({ ...item, ...patch }), CoordinationError);
  await assert.rejects(c.pull({ limit: 21 }), CoordinationError);
  assert.throws(() => validateEvent({ ...event(), payload: 'private' }), CoordinationError);
  const db = database(fixture(t));
  assert.throws(() => enqueue(db, event()), /coordination_transaction/);
  await assert.rejects(c.consume(db, item, async () => { throw Error('must never execute'); }), /coordination_handler_transaction/);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM duoflow_coord_inbox').get().n, 0);
  assert.equal(JSON.stringify(c).includes(TOKEN), false);
  db.close();
  for (const changes of [{ baseUrl: 'http://remote.example/api/v1/coordination' }, { token: 'short' }, { scopes: [{ organization_id: 'x' }] }, { acceptedSources: ['*'] }, { timeoutSeconds: NaN }])
    assert.throws(() => client(changes), /coordination_configuration/);
});

test('real HTTP center outage and stalled response retain outbox with bounded timeout', async t => {
  const server = http.createServer((_request, response) => { setTimeout(() => { if (!response.destroyed) response.end('{}'); }, 200); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const db = database(fixture(t));
  const c = client({ baseUrl: `http://127.0.0.1:${server.address().port}/api/v1/coordination`, timeoutSeconds: .05 });
  transaction(db, () => c.enqueue(db, event()));
  const started = performance.now();
  assert.deepEqual(await c.relay(db), { sent: 0, failed: 1 });
  assert.ok(performance.now() - started < 1000);
  assert.deepEqual({ ...db.prepare('SELECT sent,last_error FROM duoflow_coord_outbox').get() }, { sent: 0, last_error: 'coordination_timeout' });
  db.close();
});

test('same-file independent relay connection defers local writer conflicts and retries stable event IDs',async t=>{
  const file=fixture(t),business=database(file);
  business.exec('PRAGMA journal_mode=WAL');
  const relay=new DatabaseSync(file);relay.exec('PRAGMA busy_timeout=0');
  let offline=false;const received=[];
  const c=client({fetchImpl:async(_url,options)=>{
    const item=JSON.parse(options.body);received.push(item.event_id);
    return offline ? new Response('{}',{status:503}) : Response.json({accepted:true,duplicate:received.filter(id=>id===item.event_id).length>1,event_id:item.event_id});
  }});
  transaction(business,()=>c.enqueue(business,event()));
  business.exec("BEGIN IMMEDIATE; INSERT INTO business VALUES ('sample','pending',0)");
  let started=performance.now();
  assert.deepEqual(await c.relay(relay),{sent:0,failed:0,deferred:1});
  assert.ok(performance.now()-started<500);
  assert.equal(relay.prepare('SELECT sent FROM duoflow_coord_outbox').get().sent,0);
  business.exec('COMMIT');
  assert.deepEqual(await c.relay(relay),{sent:1,failed:0});
  assert.deepEqual(received,['event-1','event-1']);
  transaction(business,()=>c.enqueue(business,event(2)));
  business.exec('BEGIN IMMEDIATE');offline=true;
  started=performance.now();
  assert.deepEqual(await c.relay(relay),{sent:0,failed:0,deferred:1});
  assert.ok(performance.now()-started<500);
  business.exec('COMMIT');offline=false;
  assert.deepEqual(await c.relay(relay),{sent:1,failed:0});
  assert.equal(relay.prepare('SELECT SUM(sent) AS n FROM duoflow_coord_outbox').get().n,2);
  relay.close();business.close();
});
