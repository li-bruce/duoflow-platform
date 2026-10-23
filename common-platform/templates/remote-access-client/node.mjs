/** Trusted server-side client. Never import this into a browser bundle. */
export function createAccessClient({ callbackUrl, timeoutMs = 4000, fetchImpl = globalThis.fetch }) {
  const endpoint = new URL(callbackUrl);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password ||
      endpoint.hash || endpoint.search || endpoint.pathname !== '/api/v1/access/remote-decisions') {
    throw new Error('Invalid configured access-decision endpoint');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 10000) {
    throw new Error('Access decision timeout must be 1-10000 milliseconds');
  }
  // callbackUrl comes exclusively from deployment configuration. No URL, actor,
  // role, token destination or fetch options are accepted from invocation input.
  return async function decideAccess({ ticket, requestId, operation, permission, facts }) {
    const deny = reason => ({ allowed: false, reason, policy_revision: 0 });
    if (typeof ticket !== 'string' || ticket.length === 0 || ticket.length > 12288) return deny('access_ticket_invalid');
    let body;
    try { body = JSON.stringify({ request_id: requestId, operation, permission, facts }); }
    catch { return deny('access_decision_contract'); }
    if (Buffer.byteLength(body, 'utf8') > 16384) return deny('access_decision_size');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST', redirect: 'error', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-Platform-Access': ticket }, body,
      });
      if (!response.ok) return deny('access_decision_unavailable');
      // Read bounded bytes rather than response.json() on an unbounded body.
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 4096) { await reader.cancel(); return deny('access_decision_response_size'); }
        chunks.push(Buffer.from(value));
      }
      const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (typeof result.allowed !== 'boolean' || typeof result.reason !== 'string' ||
          !Number.isInteger(result.policy_revision) || result.policy_revision < 0 ||
          (result.allowed && (result.reason !== 'allowed' || result.policy_revision < 1))) return deny('access_decision_invalid');
      return { allowed: result.allowed, reason: result.reason, policy_revision: result.policy_revision };
    } catch {
      return deny('access_decision_unavailable');
    } finally {
      clearTimeout(timer);
    }
  };
}
