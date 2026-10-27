import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { AIServiceManager, AIServiceError } from "./ai-service.mjs";
import { createModuleServer } from "./runtime.mjs";
import { createRegistration } from "./registration.mjs";

// These are deterministic transport tests, never evidence of a real model call.
const services = [{ id: "sample.ai", title: "合成分析", description: "SDK 回归", capability_ids: ["sample.analyze"], supported_sources: ["platform", "module"] }];
const profile = { name: "Synthetic provider", base_url: "https://model.example/v1", model: "synthetic-model", api_key: "SYNTHETIC_PRIVATE_API_KEY_12345", enabled: true, temperature: 0.2, max_tokens: 32, timeout_seconds: 2 };
const messages = [{ role: "user", content: "合成输入" }];
const scope = { ticket: "synthetic-access-ticket-value", requestId: "a".repeat(32), operation: "invoke", permission: "sample.analyze", facts: { project_id: "P" } };
const providerResult = (content = "synthetic answer") => ({ model: "synthetic-model", choices: [{ message: { content }, finish_reason: "stop" }], usage: { total_tokens: 8 } });
function fixture(t, options = {}) {
  const parent = path.resolve(process.env.MODULE_AI_TEST_DIR || os.tmpdir());
  fs.mkdirSync(parent, { recursive: true });
  const dataDir = fs.mkdtempSync(path.join(parent, "module-ai-test-"));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(dataDir)), parent);
    assert.equal(path.basename(dataDir).startsWith("module-ai-test-"), true);
    fs.rmSync(dataDir, { recursive: true, force: true });
  });
  const manager = new AIServiceManager({ services, dataDir, ...options });
  return { manager, dataDir };
}
function configured(t, options = {}) {
  const value = fixture(t, options);
  value.manager.putConfig("sample.ai", { expected_revision: 0, profile });
  return value;
}
const throwsCode = (code, status) => (error) => error instanceof AIServiceError && error.code === code && (status === undefined || error.status === status);

test("encrypted config restarts, CAS, no plaintext or key in public output", async (t) => {
  const { manager, dataDir } = configured(t);
  const publicConfig = manager.getConfig("sample.ai");
  assert.equal(publicConfig.profile.api_key_configured, true);
  assert.equal(JSON.stringify(publicConfig).includes(profile.api_key), false);
  assert.equal(fs.readFileSync(path.join(dataDir, "module-ai.config.enc")).includes(Buffer.from(profile.api_key)), false);
  const restarted = new AIServiceManager({ services, dataDir });
  assert.deepEqual(restarted.getConfig("sample.ai"), publicConfig);
  assert.throws(() => manager.putConfig("sample.ai", { expected_revision: 0, profile }), throwsCode("ai_config_revision_changed", 409));
  assert.throws(() => manager.putConfig("sample.ai", { expected_revision: 1, profile: { base_url: "https://other.example/v1" } }), throwsCode("ai_destination_changed"));
  assert.throws(() => manager.putConfig("sample.ai", { expected_revision: 1, profile: { name: profile.api_key } }), throwsCode("ai_configuration"));
  assert.equal(manager.putConfig("sample.ai", { expected_revision: 1, profile: { clear_api_key: true } }).profile.api_key_configured, false);
});

test("standalone uses its own API, redacts credential before sending and returning", async (t) => {
  let calls = 0;
  const { manager } = configured(t, { fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, profile.base_url + "/chat/completions");
    assert.equal(options.headers.Authorization, "Bearer " + profile.api_key);
    assert.equal(options.body.includes(profile.api_key), false);
    return Response.json(providerResult("answer " + profile.api_key));
  } });
  const { result, route } = await manager.chatWithReceipt({ serviceId: "sample.ai", messages: [{ role: "user", content: profile.api_key }] });
  assert.equal(calls, 1);
  assert.equal(result.content, "answer 【密钥已隐藏】");
  assert.equal(route.binding_revision, null);
  await manager.validate({ serviceId: "sample.ai", route });
  manager.putConfig("sample.ai", { expected_revision: 1, profile: { model: "changed-model" } });
  await assert.rejects(manager.validate({ serviceId: "sample.ai", route }), throwsCode("ai_config_revision_changed", 409));
});

for (const source of ["platform", "module"]) test(`connected ${source} selection executes only that source and validates receipt`, async (t) => {
  const calls = [];
  const route = { service_id: "sample.ai", source, binding_revision: 7, module_config_revision: source === "module" ? 1 : null, platform_config_digest: source === "platform" ? "f".repeat(64) : null };
  const { manager } = configured(t, { platformBaseUrl: "http://127.0.0.1:19000", fetchImpl: async (url, options) => {
    calls.push(new URL(url).pathname);
    if (new URL(url).hostname === "model.example") return Response.json(providerResult());
    assert.equal(options.headers["X-Platform-Access"], scope.ticket);
    const body = JSON.parse(options.body);
    assert.equal(body.permission, scope.permission);
    assert.deepEqual(body.facts, scope.facts);
    return Response.json(url.endsWith("/chat") ? { ...route, result: { content: "platform answer", model: "platform-model", tool_calls: [] } } : route);
  } });
  const receipt = await manager.chatWithReceipt({ serviceId: "sample.ai", messages, scope });
  assert.deepEqual(receipt.route, route);
  assert.equal(receipt.result.content, source === "module" ? "synthetic answer" : "platform answer");
  assert.deepEqual(calls, source === "platform" ? ["/api/v1/ai/resolve", "/api/v1/ai/chat", "/api/v1/ai/validate"] : ["/api/v1/ai/resolve", "/v1/chat/completions", "/api/v1/ai/validate"]);
  await manager.validate({ serviceId: "sample.ai", route, scope });
  assert.equal(calls.at(-1), "/api/v1/ai/validate");
});

test("connected has no standalone fallback when ticket or central service is missing", async (t) => {
  let calls = 0;
  const { manager } = configured(t, { platformBaseUrl: "http://localhost:19000", fetchImpl: async () => { calls++; throw new Error(profile.api_key); } });
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages }), throwsCode("ai_access_scope", 403));
  assert.equal(calls, 0);
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages, scope }), throwsCode("ai_platform_unavailable"));
  assert.equal(calls, 1);
});

test("in-flight local profile update discards the old provider answer", async (t) => {
  let manager;
  ({ manager } = configured(t, { fetchImpl: async () => {
    manager.putConfig("sample.ai", { expected_revision: 1, profile: { model: "new-model" } });
    return Response.json(providerResult());
  } }));
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages }), throwsCode("ai_config_revision_changed", 409));
});

for (const rejection of ["binding", "grant"]) test(`post-call ${rejection} change denies returned result`, async (t) => {
  const route = { service_id: "sample.ai", source: "module", binding_revision: 3, module_config_revision: 1, platform_config_digest: null };
  const { manager } = configured(t, { platformBaseUrl: "http://localhost:19000", fetchImpl: async (url) => {
    if (url.endsWith("/resolve")) return Response.json(route);
    if (url.endsWith("/validate")) return rejection === "grant" ? Response.json({ error: { message: profile.api_key } }, { status: 403 }) : Response.json({ ...route, binding_revision: 4 });
    return Response.json(providerResult());
  } });
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages, scope }), throwsCode(rejection === "grant" ? "ai_platform_rejected" : "ai_binding_revision_changed", rejection === "grant" ? 403 : 409));
});

test("module route pins config revision before provider network I/O", async (t) => {
  let calls = 0;
  const { manager } = configured(t, { platformBaseUrl: "http://localhost:19000", fetchImpl: async () => {
    calls++;
    return Response.json({ service_id: "sample.ai", source: "module", binding_revision: 1, module_config_revision: 2, platform_config_digest: null });
  } });
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages, scope }), throwsCode("ai_config_revision_changed", 409));
  assert.equal(calls, 1);
});

for (const status of [302, 401, 403, 429, 500]) test(`provider ${status} cannot leak its error body or trigger a second call`, async (t) => {
  let calls = 0;
  const { manager } = configured(t, { fetchImpl: async () => {
    calls++; return Response.json({ error: { message: profile.api_key } }, { status });
  } });
  await assert.rejects(manager.chat({ serviceId: "sample.ai", messages }), (error) => error instanceof AIServiceError && !error.message.includes(profile.api_key));
  assert.equal(calls, 1);
});

test("control test uses fixed synthetic prompt and config CAS; no unknown controls", async (t) => {
  const { manager } = configured(t, { fetchImpl: async () => Response.json(providerResult()) });
  const answer = await manager.control("sample.ai", "POST", "test", { expected_revision: 1 });
  assert.equal(answer.ok, true);
  await assert.rejects(manager.control("sample.ai", "POST", "test", {}), throwsCode("ai_configuration"));
  await assert.rejects(manager.control("sample.ai", "POST", "config", {}), throwsCode("ai_control_not_found"));
});

test("runtime requires real AI manager, service Bearer protects configuration", async (t) => {
  const { manager } = fixture(t);
  const schema = { type: "object", properties: {}, additionalProperties: false };
  const manifest = { protocol_version: "1.2", module: { id: "sample", version: "1.0.0" }, capabilities: [{ id: "sample.analyze", version: "1.0.0", input_schema: schema, output_schema: schema }] };
  const registration = { registration_version: "1.0", module_id: "sample", module_version: "1.0.0", workflow_operations: [], permissions: { mode: "platform_access" }, ai_services: services };
  const arguments_ = { manifest, registration, token: "synthetic-private-service-token", handlers: { "sample.analyze": () => ({ summary: "done", data: {} }) } };
  assert.throws(() => createModuleServer(arguments_), /AI/);
  const server = createModuleServer({ ...arguments_, aiManager: manager });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}/platform/v1/ai/services/sample.ai/config`;
  assert.equal((await fetch(url)).status, 401);
  const response = await fetch(url, { headers: { Authorization: `Bearer ${arguments_.token}` } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { revision: 0, profile: null });
  const handshake = await fetch(url.replace(/ai\/services\/sample.ai\/config$/, "handshake"), {
    method: "POST", headers: { Authorization: `Bearer ${arguments_.token}`, "Content-Type": "application/json" }, body: "{}",
  });
  assert.equal(handshake.status, 409);
  assert.equal((await handshake.json()).error.code, "ai_platform_not_configured");
});

for (const change of ["digest", "grant"]) test(`final commit guard rejects later platform ${change} change`, async (t) => {
  let changed = false, committed = false;
  const route = { service_id: "sample.ai", source: "platform", binding_revision: 1, module_config_revision: null, platform_config_digest: "a".repeat(64) };
  const { manager } = configured(t, { platformBaseUrl: "http://localhost:19000", fetchImpl: async (url, options) => {
    if (url.endsWith("/chat")) {
      assert.equal(JSON.parse(options.body).platform_config_digest, route.platform_config_digest);
      return Response.json({ ...route, result: { content: "answer", model: "platform-model", tool_calls: [] } });
    }
    if (changed && change === "grant") return Response.json({}, { status: 403 });
    return Response.json(changed ? { ...route, platform_config_digest: "b".repeat(64) } : route);
  } });
  const receipt = await manager.chatWithReceipt({ serviceId: "sample.ai", messages, scope });
  changed = true; // Domain/source-read work happened after chat, before commit.
  await assert.rejects((async () => {
    await manager.validate({ serviceId: "sample.ai", scope, route: receipt.route });
    committed = true;
  })(), throwsCode(change === "grant" ? "ai_platform_rejected" : "ai_binding_revision_changed"));
  assert.equal(committed, false);
});

test("text contract is checked locally before any outbound request", async (t) => {
  let calls = 0;
  const { manager } = configured(t, { fetchImpl: async () => { calls++; return Response.json(providerResult()); } });
  for (const input of [Array.from({ length: 65 }, () => messages[0]), [{ role: "user", content: "x".repeat(16001) }], [{ role: "user", content: "x", tools: [] }]])
    await assert.rejects(manager.chat({ serviceId: "sample.ai", messages: input }), throwsCode("ai_request"));
  assert.equal(calls, 0);
});

test("AI declaration automatically negotiates its feature; old modules do not", () => {
  const schema = { type: "object" };
  const manifest = { protocol_version: "1.2", module: { id: "sample", version: "1.0.0" }, capabilities: [{ id: "sample.analyze", input_schema: schema, output_schema: schema }] };
  const registration = { registration_version: "1.0", module_id: "sample", module_version: "1.0.0", workflow_operations: [], permissions: { mode: "platform_access" }, ai_services: services };
  const registrar = createRegistration({ manifest, registration, handlers: { "sample.analyze": () => {} }, compile: () => {} });
  const offer = { registration_version: "1.0", nonce: "a".repeat(32), contract_digest: "b".repeat(64), manifest, registration,
    platform_features: ["invoke-1.1", "shared-hosts-1.0", "registration-1.0", "access-1.0"] };
  assert.throws(() => registrar.confirm(offer), { code: "registration_contract" });
  offer.platform_features.push("ai-services-1.0");
  assert.equal(registrar.confirm(offer).accepted, true);
  assert.throws(() => createRegistration({ manifest, registration: { ...registration, ai_services: [{ ...services[0], capability_ids: ["sample.missing"] }] }, handlers: { "sample.analyze": () => {} }, compile: () => {} }), { code: "registration_contract" });
});
