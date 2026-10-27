/** Server-only AI services v1. No credentials enter registrations or business receipts.
 * One process owns dataDir. The local key protects a copied config, not an attacker
 * who reads the whole private directory; restrict its ACL on Windows as well.
 */
import fs from "node:fs";
import path from "node:path";
import { randomBytes, createCipheriv, createDecipheriv } from "node:crypto";
import { isIP } from "node:net";

export class AIServiceError extends Error {
  constructor(code, status = 503) {
    super(code);
    this.code = code;
    this.status = status;
  }
}
const need = (ok, code = "ai_configuration", status = 422) => {
  if (!ok) throw new AIServiceError(code, status);
};
const plain = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const identifier = (value) => typeof value === "string" && /^[a-z][a-z0-9_.-]{2,119}$/.test(value);
const scopeIdentifier = (value) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value);
function factsCheck(value) {
  const factString = (entry) => typeof entry === "string" && [...entry].length > 0 && [...entry].length <= 160;
  return plain(value) && Object.keys(value).length <= 32 && Object.entries(value).every(([key, item]) =>
    /^[a-z][a-z0-9_]{0,79}$/.test(key) && (factString(item) || Array.isArray(item) && item.length > 0 && item.length <= 20 &&
      item.every(factString) && new Set(item).size === item.length));
}
const PUBLIC = ["id", "name", "base_url", "model", "enabled", "temperature", "max_tokens", "timeout_seconds"];
const PROFILE_FIELDS = new Set([...PUBLIC.filter((key) => key !== "id"), "api_key", "clear_api_key"]);
const FINISH = new Set(["stop", "length", "max_tokens", "tool_calls", "content_filter", "function_call"]);
const REDACTED = "【密钥已隐藏】";
function redact(value, secrets) {
  if (typeof value === "string") {
    for (const secret of [...new Set(secrets)].filter(Boolean).sort((a, b) => b.length - a.length))
      value = value.split(secret).join(REDACTED);
    return value;
  }
  if (Array.isArray(value)) return value.map((child) => redact(child, secrets));
  if (plain(value)) return Object.fromEntries(Object.entries(value).map(([key, child]) => [redact(key, secrets), redact(child, secrets)]));
  return value;
}
function baseURL(value, originOnly = false) {
  need(typeof value === "string" && value.length <= 2048 && !/[\s\\%?#]/u.test(value), "ai_configuration");
  let url;
  try { url = new URL(value); } catch { throw new AIServiceError("ai_configuration", 422); }
  const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  const loopback = host === "localhost" || host === "::1" || (isIP(host) === 4 && host.split(".")[0] === "127");
  need(["http:", "https:"].includes(url.protocol) && !url.username && !url.password &&
    (url.protocol === "https:" || loopback) && (!originOnly || url.pathname === "/"), "ai_configuration");
  return (originOnly ? url.origin : url.href).replace(/\/$/, "");
}
function text(value, maximum) {
  need(typeof value === "string" && value.trim().length > 0 && value.length <= maximum && !/[\u0000-\u001f\u007f]/u.test(value));
  return value.trim();
}
function number(value, min, max, integer = false) {
  need(typeof value === "number" && Number.isFinite(value) && value >= min && value <= max && (!integer || Number.isSafeInteger(value)));
  return value;
}
function messagesCheck(messages) {
  need(Array.isArray(messages) && messages.length > 0 && messages.length <= 64 && messages.every((entry) =>
    plain(entry) && Object.keys(entry).every((key) => ["role", "content"].includes(key)) &&
    ["system", "developer", "user", "assistant"].includes(entry.role) && typeof entry.content === "string" && [...entry.content].length <= 16000), "ai_request", 422);
  need(Buffer.byteLength(JSON.stringify(messages)) <= 60000, "ai_request_size", 413);
}
function resultCheck(value) {
  need(plain(value) && typeof value.content === "string" && typeof value.model === "string" &&
    Array.isArray(value.tool_calls) && value.tool_calls.length === 0 &&
    Object.keys(value).every((key) => ["content", "model", "tool_calls", "finish_reason", "usage"].includes(key)) &&
    (!Object.hasOwn(value, "finish_reason") || FINISH.has(value.finish_reason)) &&
    (!Object.hasOwn(value, "usage") || (plain(value.usage) && Object.entries(value.usage).every(([key, count]) =>
      ["prompt_tokens", "completion_tokens", "total_tokens"].includes(key) && Number.isSafeInteger(count) && count >= 0))), "ai_response", 502);
  return value;
}

export class AIServiceManager {
  #services; #directory; #filename; #keyfile; #platform; #fetch;
  constructor({ services, dataDir, platformBaseUrl = null, fetchImpl = globalThis.fetch }) {
    need(Array.isArray(services) && services.length > 0 && services.length <= 16, "ai_configuration", 500);
    need(services.every((service) => plain(service) && identifier(service.id) &&
      typeof service.title === "string" && service.title.length > 0 && [...service.title].length <= 200 &&
      typeof service.description === "string" && service.description.length > 0 && [...service.description].length <= 2000 &&
      Array.isArray(service.capability_ids) && service.capability_ids.length > 0 && service.capability_ids.length <= 64 &&
      service.capability_ids.every(identifier) && new Set(service.capability_ids).size === service.capability_ids.length &&
      Array.isArray(service.supported_sources) && service.supported_sources.length > 0 &&
      service.supported_sources.every((source) => ["platform", "module"].includes(source)) &&
      new Set(service.supported_sources).size === service.supported_sources.length &&
      Object.keys(service).sort().join() === "capability_ids,description,id,supported_sources,title") &&
      new Set(services.map((service) => service.id)).size === services.length, "ai_configuration", 500);
    need(typeof dataDir === "string" && dataDir.length > 0 && typeof fetchImpl === "function", "ai_configuration", 500);
    this.#services = new Map(services.map((service) => [service.id, structuredClone(service)]));
    this.#directory = path.resolve(dataDir);
    fs.mkdirSync(this.#directory, { recursive: true, mode: 0o700 });
    this.#filename = path.join(this.#directory, "module-ai.config.enc");
    this.#keyfile = path.join(this.#directory, "module-ai.key");
    this.#platform = platformBaseUrl === null ? null : baseURL(platformBaseUrl, true);
    this.#fetch = fetchImpl;
    this.#read();
  }
  get services() { return structuredClone([...this.#services.values()]); }
  get platformConnected() { return this.#platform !== null; }
  #service(id) { need(this.#services.has(id), "ai_service_unknown", 404); return this.#services.get(id); }
  #key(create = false) {
    try {
      if (create && !fs.existsSync(this.#keyfile)) {
        const descriptor = fs.openSync(this.#keyfile, "wx", 0o600);
        try { fs.writeFileSync(descriptor, randomBytes(32)); fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
      }
      const key = fs.readFileSync(this.#keyfile);
      need(key.length === 32, "ai_secret_unavailable", 503);
      return key;
    } catch { throw new AIServiceError("ai_secret_unavailable"); }
  }
  #read() {
    if (!fs.existsSync(this.#filename)) return { version: 1, services: {} };
    try {
      const buffer = fs.readFileSync(this.#filename);
      need(buffer.length >= 29 && buffer.length <= 1048576, "ai_config_corrupt", 503);
      const decipher = createDecipheriv("aes-256-gcm", this.#key(), buffer.subarray(0, 12));
      decipher.setAuthTag(buffer.subarray(12, 28));
      const state = JSON.parse(Buffer.concat([decipher.update(buffer.subarray(28)), decipher.final()]).toString("utf8"));
      need(state.version === 1 && plain(state.services) && Object.values(state.services).every((entry) =>
        plain(entry) && Number.isSafeInteger(entry.revision) && entry.revision >= 1 && plain(entry.profile)), "ai_config_corrupt", 503);
      return state;
    } catch { throw new AIServiceError("ai_config_corrupt"); }
  }
  #write(state) {
    const temporary = path.join(this.#directory, `.module-ai-${randomBytes(12).toString("hex")}`);
    try {
      const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", this.#key(true), iv);
      const payload = Buffer.concat([cipher.update(JSON.stringify(state), "utf8"), cipher.final()]);
      const descriptor = fs.openSync(temporary, "wx", 0o600);
      try { fs.writeFileSync(descriptor, Buffer.concat([iv, cipher.getAuthTag(), payload])); fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
      fs.renameSync(temporary, this.#filename);
    } catch (error) {
      if (error instanceof AIServiceError) throw error;
      throw new AIServiceError("ai_config_write_failed");
    } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  #secrets(state = this.#read()) { return Object.values(state.services).map((entry) => entry.profile.api_key).filter(Boolean); }
  getConfig(serviceId) {
    this.#service(serviceId);
    const entry = this.#read().services[serviceId];
    return entry ? { revision: entry.revision, profile: { ...Object.fromEntries(PUBLIC.map((key) => [key, entry.profile[key]])), api_key_configured: Boolean(entry.profile.api_key) } } : { revision: 0, profile: null };
  }
  putConfig(serviceId, { expected_revision, profile } = {}) {
    this.#service(serviceId);
    const state = this.#read(), previous = state.services[serviceId] || { revision: 0, profile: {} };
    need(Number.isSafeInteger(expected_revision) && expected_revision >= 0 && plain(profile) &&
      Object.keys(profile).every((key) => PROFILE_FIELDS.has(key)) &&
      (!Object.hasOwn(profile, "clear_api_key") || typeof profile.clear_api_key === "boolean"), "ai_configuration", 422);
    need(expected_revision === previous.revision, "ai_config_revision_changed", 409);
    const merged = { enabled: true, temperature: 0.2, max_tokens: 2048, timeout_seconds: 45, ...previous.profile, ...profile };
    need(typeof merged.enabled === "boolean");
    const stored = { id: serviceId, name: text(merged.name, 120), base_url: baseURL(merged.base_url), model: text(merged.model, 200), enabled: merged.enabled,
      temperature: number(merged.temperature, 0, 2), max_tokens: number(merged.max_tokens, 1, 131072, true), timeout_seconds: number(merged.timeout_seconds, 1, 300) };
    need(profile.api_key === undefined || (typeof profile.api_key === "string" && profile.api_key.length <= 8192));
    const supplied = (profile.api_key || "").trim();
    need(!/[^\x21-\x7e]/u.test(supplied) && !(profile.clear_api_key && supplied));
    need(!previous.profile.api_key || previous.profile.base_url === stored.base_url || supplied || profile.clear_api_key, "ai_destination_changed", 422);
    const secret = supplied || (profile.clear_api_key ? "" : previous.profile.api_key || "");
    const secrets = [...this.#secrets(state), supplied].filter(Boolean);
    need(!secrets.some((key) => Object.values(stored).some((value) => String(value).includes(key))), "ai_configuration", 422);
    need(!supplied || !Object.values(state.services).some((entry) => PUBLIC.some((key) => String(entry.profile[key]).includes(supplied))), "ai_configuration", 422);
    state.services[serviceId] = { revision: previous.revision + 1, profile: { ...stored, api_key: secret } };
    this.#write(state);
    return this.getConfig(serviceId);
  }
  async #request(url, payload, headers, timeout, provider = false) {
    const body = JSON.stringify(payload);
    need(Buffer.byteLength(body) <= (provider || url.endsWith("/chat") ? 65536 : 16384), "ai_request_size", 413);
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout * 1000);
    try {
      const response = await this.#fetch(url, { method: "POST", redirect: "error", signal: controller.signal,
        headers: { "Content-Type": "application/json", "Accept-Encoding": "identity", ...headers }, body });
      if (!response.ok) {
        await response.body?.cancel();
        const status = response.status;
        throw new AIServiceError(provider ? (status === 401 || status === 403 ? "ai_provider_auth" : status === 429 ? "ai_provider_limited" : "ai_provider_failed") : "ai_platform_rejected", provider ? 502 : status >= 400 && status <= 599 ? status : 502);
      }
      need(!response.headers.get("content-encoding") || response.headers.get("content-encoding") === "identity", "ai_response", 502);
      const reader = response.body.getReader();
      let size = 0; const chunks = [];
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 4194304) { await reader.cancel(); throw new AIServiceError("ai_response_size", 502); }
        chunks.push(Buffer.from(value));
      }
      return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch (error) {
      if (error instanceof AIServiceError) throw error;
      throw new AIServiceError(provider ? "ai_provider_unavailable" : "ai_platform_unavailable");
    } finally { clearTimeout(timer); }
  }
  #scope(scope, serviceId) {
    need(plain(scope) && typeof scope.ticket === "string" && scope.ticket.length > 0 && scope.ticket.length <= 12288 &&
      scopeIdentifier(scope.requestId) && scopeIdentifier(scope.operation) && scopeIdentifier(scope.permission) && factsCheck(scope.facts), "ai_access_scope", 403);
    return { service_id: serviceId, request_id: scope.requestId, operation: scope.operation, permission: scope.permission, facts: structuredClone(scope.facts) };
  }
  #route(route, serviceId) {
    need(plain(route) && route.service_id === serviceId && this.#service(serviceId).supported_sources.includes(route.source) &&
      Number.isSafeInteger(route.binding_revision) && route.binding_revision >= 1 &&
      (route.source === "platform" ? route.module_config_revision === null &&
        typeof route.platform_config_digest === "string" && /^[0-9a-f]{64}$/.test(route.platform_config_digest)
        : Number.isSafeInteger(route.module_config_revision) && route.module_config_revision >= 0 && route.platform_config_digest === null), "ai_route_response", 502);
    return route;
  }
  async #ownChat(serviceId, messages, revision) {
    const entry = this.#read().services[serviceId];
    need(entry && entry.revision === revision, "ai_config_revision_changed", 409);
    need(entry.profile.enabled, "ai_service_disabled", 409);
    const profile = entry.profile, secrets = this.#secrets();
    const payload = await this.#request(profile.base_url + "/chat/completions", { model: profile.model, messages: redact(messages, secrets),
      temperature: profile.temperature, max_tokens: profile.max_tokens, stream: false }, profile.api_key ? { Authorization: `Bearer ${profile.api_key}` } : {}, profile.timeout_seconds, true);
    const choice = payload?.choices?.[0], message = choice?.message;
    need(plain(message) && (message.content === null || typeof message.content === "string") &&
      (!message.tool_calls || (Array.isArray(message.tool_calls) && message.tool_calls.length === 0)), "ai_response", 502);
    const result = { content: message.content || "", tool_calls: [], model: payload.model || profile.model };
    if (FINISH.has(choice.finish_reason)) result.finish_reason = choice.finish_reason;
    if (plain(payload.usage)) result.usage = Object.fromEntries(Object.entries(payload.usage).filter(([key, value]) =>
      ["prompt_tokens", "completion_tokens", "total_tokens"].includes(key) && Number.isSafeInteger(value) && value >= 0));
    need(this.getConfig(serviceId).revision === revision, "ai_config_revision_changed", 409);
    return redact(resultCheck(result), [...secrets, ...this.#secrets()]);
  }
  async chatWithReceipt({ serviceId, messages, scope = null }) {
    const service = this.#service(serviceId);
    messagesCheck(messages);
    if (!this.#platform) {
      need(service.supported_sources.includes("module"), "ai_source_unsupported", 409);
      const revision = this.getConfig(serviceId).revision;
      const result = await this.#ownChat(serviceId, structuredClone(messages), revision);
      return { result, route: { service_id: serviceId, source: "module", binding_revision: null, module_config_revision: revision, platform_config_digest: null } };
    }
    const body = this.#scope(scope, serviceId), ticket = scope.ticket, headers = { "X-Platform-Access": ticket };
    messages = redact(structuredClone(messages), [ticket]);
    const route = this.#route(await this.#request(this.#platform + "/api/v1/ai/resolve", body, headers, 15), serviceId);
    const selected = { ...body, binding_revision: route.binding_revision, module_config_revision: route.module_config_revision, platform_config_digest: route.platform_config_digest };
    let result;
    if (route.source === "platform") {
      const answer = await this.#request(this.#platform + "/api/v1/ai/chat", { ...selected, messages: redact(messages, this.#secrets()) }, headers, 310);
      this.#route(answer, serviceId);
      need(answer.source === route.source && answer.binding_revision === route.binding_revision && answer.module_config_revision === route.module_config_revision && answer.platform_config_digest === route.platform_config_digest, "ai_binding_revision_changed", 409);
      result = resultCheck(answer.result);
    } else result = await this.#ownChat(serviceId, structuredClone(messages), route.module_config_revision);
    const after = this.#route(await this.#request(this.#platform + "/api/v1/ai/validate", selected, headers, 15), serviceId);
    need(after.source === route.source && after.binding_revision === route.binding_revision && after.module_config_revision === route.module_config_revision && after.platform_config_digest === route.platform_config_digest, "ai_binding_revision_changed", 409);
    if (route.source === "module") need(this.getConfig(serviceId).revision === route.module_config_revision, "ai_config_revision_changed", 409);
    const safe = redact(result, [...this.#secrets(), ticket]);
    return { result: resultCheck(safe), route: { service_id: serviceId, source: route.source,
      binding_revision: route.binding_revision, module_config_revision: route.module_config_revision, platform_config_digest: route.platform_config_digest } };
  }
  async chat(options) { return (await this.chatWithReceipt(options)).result; }
  /** Call again immediately before a business commit; chat's checks do not cover
   * later domain/source-read work. This is not a distributed atomic transaction. */
  async validate({ serviceId, scope = null, route }) {
    this.#service(serviceId);
    if (!this.#platform) {
      need(plain(route) && route.service_id === serviceId && route.source === "module" && route.binding_revision === null && route.platform_config_digest === null &&
        Number.isSafeInteger(route.module_config_revision) && this.getConfig(serviceId).revision === route.module_config_revision,
        "ai_config_revision_changed", 409);
      return structuredClone(route);
    }
    this.#route(route, serviceId);
    const selected = { ...this.#scope(scope, serviceId), binding_revision: route.binding_revision, module_config_revision: route.module_config_revision, platform_config_digest: route.platform_config_digest };
    const after = this.#route(await this.#request(this.#platform + "/api/v1/ai/validate", selected,
      { "X-Platform-Access": scope.ticket }, 15), serviceId);
    need(after.source === route.source && after.binding_revision === route.binding_revision && after.module_config_revision === route.module_config_revision && after.platform_config_digest === route.platform_config_digest,
      "ai_binding_revision_changed", 409);
    if (route.source === "module") need(this.getConfig(serviceId).revision === route.module_config_revision, "ai_config_revision_changed", 409);
    return { service_id: serviceId, source: after.source, binding_revision: after.binding_revision, module_config_revision: after.module_config_revision, platform_config_digest: after.platform_config_digest };
  }
  async testConnection(serviceId, expectedRevision) {
    this.#service(serviceId);
    const revision = this.getConfig(serviceId).revision;
    need(Number.isSafeInteger(expectedRevision) && expectedRevision === revision, "ai_config_revision_changed", 409);
    const started = performance.now();
    const result = await this.#ownChat(serviceId, [{ role: "user", content: "This is a synthetic connection test. Reply with CONNECTION_OK only." }], revision);
    return { ok: true, message: "连接成功，已收到供应商响应。", latency_ms: Math.round(performance.now() - started), model: result.model, response_preview: result.content.slice(0, 240) };
  }
  /** Call only AFTER the HTTP adapter verifies its private service Bearer. */
  async control(serviceId, method, action, body = {}) {
    if (action === "config" && method === "GET") return this.getConfig(serviceId);
    if (action === "config" && method === "PUT") {
      need(plain(body) && Object.keys(body).sort().join() === "expected_revision,profile");
      return this.putConfig(serviceId, body);
    }
    if (action === "test" && method === "POST") {
      need(plain(body) && Object.keys(body).join() === "expected_revision");
      return this.testConnection(serviceId, body.expected_revision);
    }
    throw new AIServiceError("ai_control_not_found", 404);
  }
}
