/** Minimal read-only remote module showing repository facts + central authorization.
 * Synthetic records only. No credentials or tickets are logged or persisted.
 */
import http from 'node:http';
import crypto from 'node:crypto';
import { createAccessClient } from './node.mjs';

const token = process.env.PLATFORM_SERVICE_TOKEN;
if (!token || token.length < 24) throw new Error('Configure a private service credential of at least 24 characters');
const decide = createAccessClient({ callbackUrl: process.env.PLATFORM_ACCESS_DECISION_URL });
const moduleId = 'example-access';
const capabilityId = `${moduleId}.item.read`;
const permission = capabilityId;
const ownerId = process.env.ACCESS_EXAMPLE_OWNER_ID || 'synthetic-owner';
const selfTest = process.env.ACCESS_EXAMPLE_SELF_TEST === '1';
const records = new Map([
  ['alpha', { id: 'alpha', project_id: 'P', owner_id: ownerId, title: '合成项目 P 的记录' }],
  ['beta', { id: 'beta', project_id: 'Q', owner_id: ownerId, title: '合成项目 Q 的记录' }],
]);
const manifest = {
  protocol_version: '1.2', module: { id: moduleId, version: '1.0.0', name: 'Node 统一权限示例', description: '从服务自有仓库读取事实，调用主框架作范围授权判断。' },
  capabilities: [{ id: capabilityId, version: '1.0.0', title: '读取合成记录', description: '只有当前项目授权覆盖的记录才返回内容。',
    kind: 'query', roles: ['member'], execution: { mode: 'background', confirmation: false },
    input_schema: { type: 'object', properties: { object_id: { type: 'string', enum: ['alpha', 'beta'] } }, required: ['object_id'], additionalProperties: false },
    output_schema: { type: 'object', properties: { allowed: { type: 'boolean' }, reason: { type: 'string' }, record: { type: ['object', 'null'] }, diagnostic_checks: { type: 'object' } }, required: ['allowed', 'reason', 'record', 'diagnostic_checks'], additionalProperties: false },
    presentation: { page: { renderer: 'json' }, conversation: { renderer: 'json' } } }],
  surfaces: { page_tools: [{ id: 'read', title: '合成记录', description: '范围授权读取。', kind: 'form', capability_ids: [capabilityId] }], conversation: { capability_ids: [capabilityId], starter_prompts: ['读取当前项目的合成记录'] } },
};
const stats = { invokes: 0, callbacks: 0, wrong_operation_denied: 0, denied_reads: 0, rechecks: 0 };
let previousDecision;
function auth(req) {
  const actual = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from(`Bearer ${token}`);
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}
function send(res, status, data) { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); }
async function checked(args) { stats.callbacks++; return decide(args); }
const server = http.createServer(async (req, res) => {
  try {
    if (!auth(req)) return send(res, 401, { error: { code: 'service_auth', message: '服务认证失败' } });
    if (req.method === 'GET' && req.url === '/platform/v1/manifest') return send(res, 200, manifest);
    if (req.method === 'GET' && req.url === '/platform/v1/health') return send(res, 200, { status: 'ok' });
    if (selfTest && req.method === 'GET' && req.url === '/platform/v1/test-stats') return send(res, 200, stats);
    if (selfTest && req.method === 'POST' && req.url === '/platform/v1/test-recheck') {
      stats.rechecks++;
      return send(res, 200, previousDecision ? await checked(previousDecision) : { allowed: false, reason: 'no_previous_call', policy_revision: 0 });
    }
    if (req.method !== 'POST' || req.url !== '/platform/v1/invoke') return send(res, 404, { error: { code: 'not_found', message: '接口不存在' } });
    const chunks = []; let length = 0;
    for await (const chunk of req) { length += chunk.length; if (length > 16384) return send(res, 413, { error: { code: 'request_size', message: '请求过大' } }); chunks.push(chunk); }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (body.protocol_version !== '1.1' || body.capability_id !== capabilityId || body.capability_version !== '1.0.0' ||
        !/^[a-f0-9]{32}$/.test(body.request_id || '') || !body.context?.actor?.id ||
        !body.input || Object.keys(body.input).join() !== 'object_id' || !records.has(body.input.object_id)) {
      return send(res, 422, { error: { code: 'input_contract', message: '请求不符合契约' } });
    }
    stats.invokes++;
    // This repository lookup, never the supplied actor or input.project_id, owns facts.
    const record = records.get(body.input.object_id);
    const args = { ticket: req.headers['x-platform-access'], requestId: body.request_id, operation: 'invoke', permission,
      facts: { project_id: record.project_id, owner_id: record.owner_id } };
    const decision = await checked(args);
    const checks = {};
    if (selfTest) {
      checks.wrong_operation_denied = !(await checked({ ...args, operation: 'objects.read' })).allowed;
      if (checks.wrong_operation_denied) stats.wrong_operation_denied++;
      previousDecision = args; // Synthetic diagnostic only, memory for this short test.
    }
    if (!decision.allowed) stats.denied_reads++;
    send(res, 200, { protocol_version: '1.1', request_id: body.request_id, capability_id: capabilityId, capability_version: '1.0.0',
      summary: decision.allowed ? '已读取授权项目内的合成记录' : '当前授权范围不允许读取此记录',
      data: { allowed: decision.allowed, reason: decision.reason, record: decision.allowed ? record : null, diagnostic_checks: checks } });
  } catch { send(res, 500, { error: { code: 'handler_failed', message: '示例服务处理失败' } }); }
});
server.listen(Number(process.env.PORT || 0), '127.0.0.1', () => console.log(JSON.stringify({ ready: true, port: server.address().port })));
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close(() => process.exit(0)));
