/** Server-only registered source reads. Tickets are never returned or stored. */
export class SourceReadError extends Error {
  constructor(code, status = 503) {
    super(code); this.code = code;
    this.status = Number.isInteger(status) && status >= 400 && status <= 599 ? status : 502;
  }
}

export function createSourceReadClient({ callbackUrl, timeoutMs = 8000, fetchImpl = globalThis.fetch }) {
  const endpoint = new URL(callbackUrl);
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password ||
      endpoint.search || endpoint.hash || endpoint.pathname !== "/api/v1/access/source-reads")
    throw new SourceReadError("source_read_configuration", 500);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000)
    throw new SourceReadError("source_read_configuration", 500);
  return async function readSource({ ticket, requestId, operation, permission, facts, source, readInput }) {
    if (typeof ticket !== "string" || !ticket || ticket.length > 12288)
      throw new SourceReadError("access_ticket_invalid", 401);
    const body = JSON.stringify({ source_read_version: "1.0", request_id: requestId,
      operation, permission, facts, source, ...(readInput === undefined ? {} : { read_input: readInput }) });
    if (Buffer.byteLength(body, "utf8") > 16384) throw new SourceReadError("source_read_size", 413);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(endpoint, {
        method: "POST", redirect: "error", signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Platform-Access": ticket }, body,
      });
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > (response.ok ? 1100000 : 4096)) {
          await reader.cancel(); throw new SourceReadError("source_read_response_size", 502);
        }
        chunks.push(Buffer.from(value));
      }
      const raw = Buffer.concat(chunks).toString("utf8");
      if (raw.includes(ticket)) throw new SourceReadError("source_read_credential", 502);
      const result = JSON.parse(raw);
      if (!response.ok) {
        const code = result.error?.code;
        throw new SourceReadError(typeof code === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(code)
          ? code : "source_read_denied", response.status);
      }
      const ref = source?.object_ref, actual = result.source, projection = result.projection;
      if (result.source_read_version !== "1.0" || Object.keys(result).sort().join() !== "projection,source,source_read_version" ||
          actual?.module_id !== source.module_id || actual?.revision !== source.revision ||
          actual?.object_ref?.object_id !== ref?.object_id || actual?.object_ref?.object_type !== ref?.object_type ||
          projection?.revision !== source.revision || projection?.object_ref?.object_id !== ref?.object_id ||
          projection?.object_ref?.object_type !== ref?.object_type || typeof projection?.state !== "string" ||
          !projection?.data || typeof projection.data !== "object" || Array.isArray(projection.data) ||
          !Array.isArray(projection.allowed_actions)) throw new SourceReadError("source_read_response", 502);
      return result;
    } catch (error) {
      if (error instanceof SourceReadError) throw error;
      throw new SourceReadError("source_read_unavailable");
    } finally { clearTimeout(timer); }
  };
}
