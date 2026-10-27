/** Optional process telemetry sent directly to the independent collector. */
import os from 'node:os';
import { randomUUID } from 'node:crypto';

export function createTelemetryReporter({url, token, instanceId = os.hostname(),
  intervalSeconds = 15, metrics = () => ({}), fetchImpl = globalThis.fetch}) {
  let endpoint;
  try { endpoint = new URL(url); } catch { throw new Error('Invalid telemetry configuration'); }
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash ||
      !/^\/api\/v1\/monitor\/reports\/[a-z][a-z0-9_.-]{1,79}$/.test(endpoint.pathname) ||
      !['http:', 'https:'].includes(endpoint.protocol) ||
      (endpoint.protocol === 'http:' && !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)) ||
      typeof token !== 'string' || token.length < 24 || token.length > 4096 ||
      typeof instanceId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(instanceId) ||
      !Number.isFinite(intervalSeconds) || intervalSeconds < 1 || intervalSeconds > 300)
    throw new Error('Invalid telemetry configuration');
  const bootId = randomUUID().replaceAll('-', '');
  let sequence = 0, timer, active = null, stopped = false;
  let cpu = process.cpuUsage(), sampled = performance.now();
  const started = performance.now();
  const allowed = new Set(['inflight_requests', 'queue_depth', 'requests_total', 'errors_total']);
  let sent = 0, failed = 0, skipped = 0;
  async function report() {
    if (stopped || active) { skipped++; return; }
    active = (async () => {
      try {
        const now = performance.now(), used = process.cpuUsage(cpu);
        cpu = process.cpuUsage();
        const cpuPercent = Math.min(100, Math.max(0, (used.user + used.system) / Math.max(1, now-sampled) / 10 / os.availableParallelism()));
        sampled = now;
        const supplied = metrics();
        if (!supplied || Array.isArray(supplied) || Object.keys(supplied).some(key => !allowed.has(key)))
          throw new Error('Invalid telemetry counters');
        for (const value of Object.values(supplied))
          if (!Number.isSafeInteger(value) || value < 0) throw new Error('Invalid telemetry counters');
        const body = JSON.stringify({monitor_version:'1.0', instance_id:instanceId, boot_id:bootId,
          sequence:++sequence, status:'ready', uptime_seconds:(now-started)/1000,
          metrics:{cpu_percent:cpuPercent, rss_bytes:process.memoryUsage().rss, ...supplied}});
        const response = await fetchImpl(endpoint, {method:'POST', redirect:'error',
          signal:AbortSignal.timeout(2000), headers:{'Content-Type':'application/json', Authorization:'Bearer '+token}, body});
        await response.body?.cancel();
        if (response.ok) sent++; else failed++;
      } catch { failed++; } // Telemetry never changes a business result or logs secrets.
    })();
    try { await active; } finally { active = null; }
  }
  return {
    report,
    start() { if (!stopped && !timer) { timer=setInterval(()=>void report(), intervalSeconds*1000); timer.unref?.(); void report(); } },
    async stop() { stopped=true; clearInterval(timer); timer=undefined; if(active) await active; },
    stats() { return {sent, failed, skipped}; },
  };
}
