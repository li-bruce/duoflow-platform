import assert from "node:assert/strict";
import { test } from "node:test";
import { createSourceReadClient } from "./source-read-client.mjs";
import { createRegistration } from "./registration.mjs";

const callbackUrl = "http://127.0.0.1:8031/api/v1/access/source-reads";
const source = { module_id: "example.source", object_ref: { object_type: "example.source.snapshot", object_id: "S1" }, revision: 7 };
const input = { ticket: "synthetic-ticket", requestId: "r1", operation: "invoke", permission: "consumer.use", facts: { project_id: "P" }, source };
const result = { source_read_version: "1.0", source, projection: { object_ref: source.object_ref, revision: 7, state: "published", data: { selected: "actual" }, allowed_actions: [] } };

test("callback has a fixed URL, original target header and no persisted ticket", async () => {
  let seen;
  const read = createSourceReadClient({ callbackUrl, fetchImpl: async (url, options) => {
    seen = { url: String(url), options }; return Response.json(result);
  } });
  assert.deepEqual(await read(input), result);
  assert.equal(seen.url, callbackUrl);
  assert.equal(seen.options.redirect, "error");
  assert.equal(seen.options.headers["X-Platform-Access"], input.ticket);
  assert.equal(Object.hasOwn(JSON.parse(seen.options.body), "ticket"), false);
});

test("callback override, version mismatch and credential echo fail", async () => {
  for (const url of ["https://host/wrong", "https://user:pass@host/api/v1/access/source-reads", callbackUrl + "?url=other"])
    assert.throws(() => createSourceReadClient({ callbackUrl: url }));
  const mismatch = createSourceReadClient({ callbackUrl, fetchImpl: async () => Response.json({ ...result, source: { ...source, revision: 8 } }) });
  await assert.rejects(mismatch(input), { code: "source_read_response" });
  const echo = createSourceReadClient({ callbackUrl, fetchImpl: async () => Response.json({ ...result, projection: { ...result.projection, data: { secret: input.ticket } } }) });
  await assert.rejects(echo(input), { code: "source_read_credential" });
});

test("failed source and bounded response preserve a stable failure", async () => {
  const denied = createSourceReadClient({ callbackUrl, fetchImpl: async () => Response.json({ error: { code: "source_revision_changed" } }, { status: 409 }) });
  await assert.rejects(denied(input), { code: "source_revision_changed" });
  const large = createSourceReadClient({ callbackUrl, fetchImpl: async () => new Response(" ".repeat(1100001)) });
  await assert.rejects(large(input), { code: "source_read_response_size" });
  const offline = createSourceReadClient({ callbackUrl, fetchImpl: async () => { throw new Error("private transport detail"); } });
  await assert.rejects(offline(input), { code: "source_read_unavailable" });
});

test("consumer can require the optional feature during registration confirmation", () => {
  const manifest = { protocol_version: "1.2", module: { id: "example.consumer", version: "1.0.0" }, capabilities: [] };
  const registration = { registration_version: "1.0", module_id: "example.consumer", module_version: "1.0.0", workflow_operations: [], permissions: { mode: "capability_roles" } };
  const features = ["source-reads-1.0"];
  const registrar = createRegistration({ manifest, registration, handlers: {}, compile: () => {}, requiredPlatformFeatures: features });
  features.length = 0;
  const offer = { registration_version: "1.0", nonce: "a".repeat(32), contract_digest: "b".repeat(64), manifest, registration,
    platform_features: ["invoke-1.1", "shared-hosts-1.0", "registration-1.0"] };
  assert.throws(() => registrar.confirm(offer), { code: "registration_contract" });
  offer.platform_features.push("source-reads-1.0");
  assert.equal(registrar.confirm(offer).accepted, true);
});

test("source HTTP failures preserve safe status without exposing response details", async () => {
  for (const status of [403, 409, 422, 503]) {
    const read = createSourceReadClient({ callbackUrl, fetchImpl: async () =>
      Response.json({ error: { code: "source_fixture_error", message: "private fixture detail" } }, { status }) });
    await assert.rejects(read(input), error => error.code === "source_fixture_error" &&
      error.status === status && error.message === "source_fixture_error");
  }
});
