/** Coordination 1.0: module-owned SQLite transactional outbox/inbox.
 * initializeCoordination runs outside transactions; enqueue never commits.
 * consume uses a synchronous handler and commits before ACK. The handler must
 * not issue transaction commands or external side effects. PostgreSQL/MySQL
 * require their own transaction-native adapter, not a second SQLite database.
 */
import { createHash } from 'node:crypto';

export class CoordinationError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}
const requireThat = (ok, code = 'coordination_contract') => { if (!ok) throw new CoordinationError(code); };
const name = (value, maximum = 160, colon = true) => typeof value === 'string' && value.length >= 1 && value.length <= maximum && (colon ? /^[A-Za-z0-9_.:-]+$/ : /^[A-Za-z0-9_.-]+$/).test(value);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
const canonical = value => {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (record(value)) return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  requireThat(value === null || typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)));
  return JSON.stringify(value);
};
const scope = value => record(value) && Object.keys(value).length === 2 && ['organization_id', 'project_id'].every(key => name(value[key], 128));
const equal = (a, b) => canonical(a) === canonical(b);
const sqliteBusy = error => error?.code === 'ERR_SQLITE_ERROR' && [5, 6].includes(error.errcode & 255);

export function validateEvent(event, { delivery = false } = {}) {
  const fields = ['version', 'event_id', 'module_id', 'kind', 'machine_id', 'object_ref', 'state', 'revision', 'scope'];
  const kinds = ['state.changed', 'publication.requested', 'resource.published', 'resource.withdrawn', ...(delivery ? ['publication.approved', 'publication.rejected'] : [])];
  requireThat(record(event) && fields.every(key => Object.hasOwn(event, key)) && Object.keys(event).every(key => fields.includes(key) || key === 'approval_ref'));
  requireThat(event.version === '1.0' && kinds.includes(event.kind));
  requireThat(name(event.event_id) && name(event.module_id, 128, false) && name(event.machine_id, 160, false) && name(event.state, 128, false));
  requireThat(record(event.object_ref) && Object.keys(event.object_ref).length === 2 && name(event.object_ref.object_type, 160, false) && name(event.object_ref.object_id, 256));
  requireThat(Number.isSafeInteger(event.revision) && event.revision >= 1 && scope(event.scope));
  requireThat(!Object.hasOwn(event, 'approval_ref') || name(event.approval_ref));
  requireThat(!['publication.approved', 'publication.rejected'].includes(event.kind) || Object.hasOwn(event, 'approval_ref'));
  requireThat(Buffer.byteLength(canonical(event), 'utf8') <= 8192);
  return event;
}

export function initializeCoordination(db) {
  requireThat(db && typeof db.prepare === 'function' && !db.isTransaction, 'coordination_transaction');
  db.exec(`CREATE TABLE IF NOT EXISTS duoflow_coord_outbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
    object_key TEXT NOT NULL, event_json TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT);
    CREATE TABLE IF NOT EXISTS duoflow_coord_inbox (
    delivery_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, committed_at REAL NOT NULL);`);
}

export function enqueue(db, event) {
  requireThat(db.isTransaction, 'coordination_transaction');
  validateEvent(event);
  const encoded = canonical(event);
  const old = db.prepare('SELECT event_json FROM duoflow_coord_outbox WHERE event_id=?').get(event.event_id);
  if (old) { requireThat(old.event_json === encoded, 'coordination_event_conflict'); return false; }
  const key = canonical([event.module_id, event.machine_id, event.object_ref]);
  db.prepare('INSERT INTO duoflow_coord_outbox(event_id,object_key,event_json) VALUES (?,?,?)').run(event.event_id, key, encoded);
  return true;
}

export class CoordinationClient {
  #token;
  constructor({ baseUrl, token, moduleId, consumerId, scopes, acceptedSources = [moduleId], timeoutSeconds = 3, fetchImpl = globalThis.fetch, clock = () => Date.now()/1000 }) {
    let url;
    try { url = new URL(baseUrl); } catch { throw new CoordinationError('coordination_configuration'); }
    requireThat(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash && url.pathname.replace(/\/$/, '') === '/api/v1/coordination' && (url.protocol === 'https:' || ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)), 'coordination_configuration');
    requireThat(typeof token === 'string' && token.length >= 16 && token.length <= 512 && !/[\r\n ]/.test(token) && name(moduleId, 128, false) && name(consumerId, 128), 'coordination_configuration');
    requireThat(Number.isFinite(timeoutSeconds) && timeoutSeconds >= .05 && timeoutSeconds <= 30 && typeof fetchImpl === 'function' && typeof clock === 'function', 'coordination_configuration');
    requireThat(Array.isArray(scopes) && scopes.length > 0 && scopes.length <= 100 && scopes.every(scope), 'coordination_configuration');
    requireThat(Array.isArray(acceptedSources) && acceptedSources.length > 0 && acceptedSources.length <= 100 && acceptedSources.every(value => name(value, 128, false)), 'coordination_configuration');
    this.baseUrl = url.href.replace(/\/$/, ''); this.#token = token; this.moduleId = moduleId; this.consumerId = consumerId;
    this.scopes = JSON.parse(canonical(scopes)); this.acceptedSources = new Set(acceptedSources);
    this.timeoutSeconds = timeoutSeconds; this.fetchImpl = fetchImpl; this.clock = clock;
  }
  async request(method, suffix, payload) {
    const controller = new AbortController(); let timer;
    const expired = new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new CoordinationError('coordination_timeout')); }, this.timeoutSeconds * 1000); });
    try {
      return await Promise.race([expired, (async () => {
        const response = await this.fetchImpl(this.baseUrl + suffix, { method, redirect: 'error', signal: controller.signal,
          headers: { Authorization: 'Bearer ' + this.#token, 'Content-Type': 'application/json' },
          ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
        if (!response.ok) { await response.body?.cancel(); throw new CoordinationError('coordination_http', response.status); }
        requireThat(response.body && typeof response.body.getReader === 'function');
        const reader = response.body.getReader(), chunks = []; let size = 0;
        try {
          while (true) {
            const { done, value } = await reader.read(); if (done) break;
            size += value.byteLength; requireThat(size <= 262144, 'coordination_response_limit'); chunks.push(value);
          }
        } finally { await reader.cancel().catch(() => {}); }
        const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        requireThat(record(result)); return result;
      })()]);
    } catch (error) {
      if (error instanceof CoordinationError) throw error;
      throw new CoordinationError(controller.signal.aborted ? 'coordination_timeout' : 'coordination_transport');
    } finally { clearTimeout(timer); controller.abort(); }
  }
  inScope(event) { return this.scopes.some(item => equal(item, event.scope)); }
  enqueue(db, event) {
    validateEvent(event);
    requireThat(event.module_id === this.moduleId && this.inScope(event), 'coordination_scope');
    return enqueue(db, event);
  }
  async relay(db, { limit = 20 } = {}) {
    requireThat(!db.isTransaction, 'coordination_transaction');
    requireThat(Number.isInteger(limit) && limit >= 1 && limit <= 20);
    const rows = db.prepare(`SELECT o.event_id,o.event_json,o.attempts FROM duoflow_coord_outbox o
      WHERE o.sent=0 AND o.next_attempt<=? AND NOT EXISTS (
        SELECT 1 FROM duoflow_coord_outbox p WHERE p.sent=0 AND p.object_key=o.object_key AND p.sequence<o.sequence)
      ORDER BY o.sequence LIMIT ?`).all(this.clock(), limit);
    let sent = 0, failed = 0, deferred = 0;
    for (const row of rows) {
      try {
        const event = validateEvent(JSON.parse(row.event_json));
        requireThat(event.module_id === this.moduleId && this.inScope(event), 'coordination_scope');
        const result = await this.request('POST', '/events', event);
        requireThat(result.accepted === true && typeof result.duplicate === 'boolean' && result.event_id === event.event_id);
      } catch (error) {
        try {
          db.prepare('UPDATE duoflow_coord_outbox SET attempts=attempts+1,next_attempt=?,last_error=? WHERE event_id=? AND sent=0')
            .run(this.clock() + Math.min(300, 2 ** Math.min(row.attempts, 8)), error instanceof CoordinationError ? error.code : 'coordination_contract', row.event_id);
        } catch (storageError) {
          if (!sqliteBusy(storageError)) throw storageError;
          // Another connection owns a short business write. The committed
          // outbox stays pending; stop this batch and retry on the next pump.
          deferred++; break;
        }
        failed++; continue;
      }
      try {
        db.prepare('UPDATE duoflow_coord_outbox SET sent=1,attempts=attempts+1,last_error=NULL WHERE event_id=?').run(row.event_id);
      } catch (error) {
        if (!sqliteBusy(error)) throw error;
        // The center may already have committed. Retry the stable event ID;
        // the center returns its duplicate receipt after the local lock clears.
        deferred++; break;
      }
      sent++;
    }
    return { sent, failed, ...(deferred ? { deferred } : {}) };
  }
  validateDelivery(item) {
    requireThat(record(item) && name(item.id) && name(item.subscription_id, 256));
    requireThat(item.target_module === this.moduleId && item.consumer_id === this.consumerId, 'coordination_target');
    requireThat(['notify', 'sync'].includes(item.mode) && typeof item.lease_token === 'string' && item.lease_token.length >= 16 && item.lease_token.length <= 256 && /^[A-Za-z0-9_-]+$/.test(item.lease_token));
    requireThat(Number.isFinite(item.lease_until) && item.lease_until > 0 && Number.isSafeInteger(item.attempts) && item.attempts >= 1);
    const event = validateEvent(item.event, { delivery: true });
    requireThat(this.acceptedSources.has(event.module_id) && this.inScope(event), 'coordination_scope');
    return item;
  }
  async pull({ limit = 20 } = {}) {
    requireThat(Number.isInteger(limit) && limit >= 1 && limit <= 20);
    const result = await this.request('GET', '/deliveries?consumer_id=' + encodeURIComponent(this.consumerId) + '&limit=' + limit);
    requireThat(result.version === '1.0' && result.module_id === this.moduleId && result.consumer_id === this.consumerId && Array.isArray(result.deliveries) && result.deliveries.length <= limit);
    return result.deliveries.map(item => this.validateDelivery(item));
  }
  async consume(db, item, handler) {
    this.validateDelivery(item);
    requireThat(!db.isTransaction && typeof handler === 'function', 'coordination_transaction');
    requireThat(!['[object AsyncFunction]', '[object AsyncGeneratorFunction]'].includes(Object.prototype.toString.call(handler)), 'coordination_handler_transaction');
    const identity = Object.fromEntries(['id', 'subscription_id', 'target_module', 'mode', 'event'].map(key => [key, item[key]]));
    const fingerprint = createHash('sha256').update(canonical(identity)).digest('hex');
    let old;
    db.exec('BEGIN IMMEDIATE');
    try {
      old = db.prepare('SELECT fingerprint FROM duoflow_coord_inbox WHERE delivery_id=?').get(item.id);
      if (old) requireThat(old.fingerprint === fingerprint, 'coordination_delivery_conflict');
      else {
        const outcome = handler(db, item.event, item);
        requireThat(!outcome || typeof outcome.then !== 'function', 'coordination_handler_transaction');
        requireThat(db.isTransaction, 'coordination_handler_transaction');
        db.prepare('INSERT INTO duoflow_coord_inbox VALUES (?,?,?)').run(item.id, fingerprint, this.clock());
      }
      db.exec('COMMIT');
    } catch (error) { if (db.isTransaction) db.exec('ROLLBACK'); throw error; }
    try {
      const result = await this.request('POST', '/deliveries/' + encodeURIComponent(item.id) + '/ack', { consumer_id: this.consumerId, lease_token: item.lease_token });
      requireThat(result.acknowledged === true && typeof result.duplicate === 'boolean' && result.delivery_id === item.id);
    } catch (error) { return { applied: !old, acknowledged: false, error: error instanceof CoordinationError ? error.code : 'coordination_transport' }; }
    return { applied: !old, acknowledged: true };
  }
}
