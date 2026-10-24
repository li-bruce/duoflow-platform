/** Real isolated HTTP/SQLite tests. The central decision endpoint below is a synthetic stub,
 * not a substitute for registration, real grants, host UI, or production browser acceptance. */
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import {
  createService,
  manifest,
  registration,
  moduleGraph,
} from "../backend/server.mjs";
import { createObservationClient } from "../sdk/node/observation.mjs";
import { compile, createModuleServer } from "../sdk/node/runtime.mjs";
import { createAtomBindings } from "../sdk/node/atoms.mjs";
import {
  assertTransition,
  assertAtomResult,
} from "../sdk/node/graph-testing.mjs";

const M = manifest.module.id;
const token = "synthetic-test-service-credential-32";
const projects = [
  { project_id: "P", organization_id: "O", title: "合成 P" },
  { project_id: "Q", organization_id: "Z", title: "合成 Q" },
];
const actor = (id) => ({
  id,
  name: id,
  role: "member",
  roles: ["member"],
  organization_id: "O",
  team_ids: [],
  authorization_revision: 1,
});
const id = () => randomBytes(16).toString("hex");
async function listen(server) {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}
async function fixture(t) {
  const directory = mkdtempSync(path.join(tmpdir(), "module-kit-node-"));
  const tickets = new Map(),
    seen = [],
    revoked = new Set();
  let unavailable = false,
    denyCommit = false;
  const central = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const input = JSON.parse(body),
      claims = tickets.get(req.headers["x-platform-access"]);
    if (req.url === "/api/v1/runtime-observations/probe") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ accepted: true, nonce: input.nonce }));
      return;
    }
    seen.push(input);
    let reason = "allowed";
    if (unavailable) {
      res.writeHead(503);
      res.end("{}");
      return;
    }
    if (!claims || claims.expired) reason = "access_ticket_expired";
    else if (
      claims.requestId !== input.request_id ||
      claims.operation !== input.operation ||
      claims.permission !== input.permission
    )
      reason = "access_ticket_permission_mismatch";
    else if (revoked.has(claims.actor)) reason = "access_authorization_changed";
    else if (
      input.facts.project_id !== "P" ||
      input.facts.organization_id !== "O" ||
      claims.actor === "outsider"
    )
      reason = "grant_scope_mismatch";
    else if (
      ["edit", "submit"].some(
        (suffix) => input.permission === `${M}.${suffix}`,
      ) &&
      input.facts.author_id !== claims.actor
    )
      reason = "actor_relation_mismatch";
    else if (
      input.permission === `${M}.approve` &&
      input.facts.author_id === claims.actor
    )
      reason = "separation_of_duties";
    else if (input.permission === `${M}.approve` && claims.actor !== "reviewer")
      reason = "grant_scope_mismatch";
    else if (
      input.permission !== `${M}.read` &&
      claims.actor === "reviewer" &&
      input.permission !== `${M}.approve`
    )
      reason = "grant_scope_mismatch";
    else if (
      denyCommit &&
      input.permission === `${M}.create` &&
      seen.filter((item) => item.request_id === input.request_id).length > 1
    )
      reason = "access_authorization_changed";
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        allowed: reason === "allowed",
        reason,
        policy_revision: reason.startsWith("access_") ? 0 : 1,
      }),
    );
  });
  const centralUrl = await listen(central),
    filename = path.join(directory, "records.sqlite");
  let service = createService({
      token,
      callbackUrl: centralUrl + "/api/v1/access/remote-decisions",
      filename,
      projects,
    }),
    url = await listen(service.server);
  const makeBody = (action, input, who = "author") => ({
    protocol_version: "1.1",
    request_id: id(),
    capability_id: `${M}.${action}`,
    capability_version: "1.0.0",
    input,
    context: { workspace_id: "synthetic-workspace", actor: actor(who) },
  });
  async function request(body, operation = "invoke", options = {}) {
    const ticket = "synthetic-ticket-" + id();
    const suffix = body.capability_id?.slice(M.length + 1);
    tickets.set(ticket, {
      requestId: body.request_id,
      operation,
      permission: `${M}.${operation === "invoke" && suffix !== "list" ? suffix : "read"}`,
      actor: body.context.actor.id,
      expired: options.expired,
    });
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(!options.noTicket ? { "X-Platform-Access": ticket } : {}),
    };
    const result = await fetch(
      url +
        "/platform/v1/" +
        (operation === "invoke" ? "invoke" : `workflow/${operation}`),
      { method: "POST", headers, body: JSON.stringify(body) },
    );
    return { status: result.status, body: await result.json(), ticket };
  }
  const invoke = (action, input, who = "author", options = {}) =>
    request(makeBody(action, input, who), "invoke", options);
  const flow = (operation, input, who = "author", options = {}) =>
    request(
      {
        protocol_version: "1.0",
        request_id: id(),
        context: { actor: actor(who) },
        input,
      },
      operation,
      options,
    );
  const create = (title = "合成记录") =>
    invoke("create", { command: { project_id: "P", title, body: "内容" } });
  t.after(async () => {
    await service.close();
    await new Promise((resolve) => central.close(resolve));
    rmSync(directory, { recursive: true, force: true });
  });
  return {
    get url() {
      return url;
    },
    filename,
    tickets,
    seen,
    revoked,
    makeBody,
    request,
    invoke,
    flow,
    create,
    setUnavailable: (value) => {
      unavailable = value;
    },
    setDenyCommit: (value) => {
      denyCommit = value;
    },
    restart: async () => {
      await service.close();
      service = createService({
        token,
        callbackUrl: centralUrl + "/api/v1/access/remote-decisions",
        filename,
        projects,
      });
      url = await listen(service.server);
    },
  };
}

test("health and manifest use real HTTP and require the private service bearer", async (t) => {
  const f = await fixture(t);
  for (const endpoint of ["health", "manifest"]) {
    assert.equal((await fetch(f.url + "/platform/v1/" + endpoint)).status, 401);
    const response = await fetch(f.url + "/platform/v1/" + endpoint, {
      headers: { Authorization: `Bearer ${token}` },
    });
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(
      endpoint === "health" ? data.status : data.protocol_version,
      endpoint === "health" ? "ok" : "1.2",
    );
  }
});

test("registration checks both complete contracts and actual bindings without business writes", async (t) => {
  const f = await fixture(t),
    headers = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  assert.equal((await fetch(f.url + "/platform/v1/registration")).status, 401);
  assert.equal((await fetch(f.url + "/platform/v1/module-graph")).status, 401);
  assert.deepEqual(
    await (
      await fetch(f.url + "/platform/v1/module-graph", { headers })
    ).json(),
    moduleGraph,
  );
  assert.deepEqual(
    await (
      await fetch(f.url + "/platform/v1/registration", { headers })
    ).json(),
    registration,
  );
  const offer = {
    registration_version: "1.1",
    nonce: "a".repeat(32),
    contract_digest: "b".repeat(64),
    platform_features: [
      "invoke-1.1",
      "access-1.0",
      "shared-hosts-1.0",
      "registration-1.0",
      "registration-1.1",
      "module-graph-1.0",
      "flow-exports-1.0",
      "runtime-observation-1.0",
    ],
    manifest,
    registration,
    runtime_probe: { ticket: "c".repeat(64), nonce: "d".repeat(32) },
  };
  async function confirm(body) {
    return fetch(f.url + "/platform/v1/handshake", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  }
  const accepted = await confirm(offer);
  assert.equal(accepted.status, 200);
  const answer = await accepted.json();
  assert.equal(answer.accepted, true);
  assert.deepEqual(
    answer.capability_ids,
    manifest.capabilities.map((cap) => cap.id).sort(),
  );
  for (const field of ["manifest", "registration", "platform_features"])
    assert.equal((await confirm({ ...offer, [field]: [] })).status, 422);
  assert.equal((await confirm({})).status, 422);
  const database = new DatabaseSync(f.filename, { readOnly: true });
  assert.equal(
    database.prepare("SELECT count(*) AS n FROM records").get().n,
    0,
  );
  assert.equal(
    database.prepare("SELECT count(*) AS n FROM kit_receipts").get().n,
    0,
  );
  database.close();
  assert.equal(f.seen.length, 0);
});
test("atomic startup binding rejects missing functions and never runs business handlers", () => {
  let calls = 0;
  const handlers = Object.fromEntries(
    manifest.capabilities.map((cap) => [
      cap.id,
      () => {
        calls++;
      },
    ]),
  );
  const bindings = createAtomBindings({
    graph: moduleGraph,
    manifest,
    handlers,
  });
  assert.equal(
    bindings.forCapability(manifest.capabilities[0].id).kind,
    "backend",
  );
  const copy = bindings.document;
  copy.atoms.length = 0;
  assert.ok(bindings.document.atoms.length > 0);
  assert.throws(() =>
    createAtomBindings({ graph: moduleGraph, manifest, handlers: {} }),
  );
  assert.equal(calls, 0);
});
test("strict request validation and server-owned project facts reject injected scope", async (t) => {
  const f = await fixture(t);
  assert.equal(
    (
      await f.invoke("create", {
        command: { project_id: "UNKNOWN", title: "x", body: "" },
      })
    ).status,
    422,
  );
  assert.equal(
    (
      await f.invoke("create", {
        command: {
          project_id: "P",
          organization_id: "FORGED",
          title: "x",
          body: "",
        },
      })
    ).status,
    422,
  );
  const malformed = f.makeBody("create", {
    command: { project_id: "P", title: "x", body: "" },
  });
  malformed.context.actor.extra = "forged";
  assert.equal((await f.request(malformed)).status, 422);
  const created = await f.create();
  assert.equal(created.status, 200);
  assert.equal(created.body.data.projection.data.organization_id, "O");
  assert.ok(f.seen.every((item) => !JSON.stringify(item).includes("FORGED")));
});
test("missing, expired and unavailable central decisions fail closed even for empty lists", async (t) => {
  const f = await fixture(t);
  for (const options of [{ noTicket: true }, { expired: true }])
    assert.equal(
      (await f.flow("objects.list", {}, "author", options)).status,
      403,
    );
  f.setUnavailable(true);
  assert.equal((await f.create()).status, 403);
  assert.equal((await f.flow("objects.list", {})).status, 403);
  f.setUnavailable(false);
  assert.deepEqual((await f.flow("objects.list", {})).body.data.objects, []);
});
test("draft -> submitted -> approved requires scoped non-author reviewer; formal states persist", async (t) => {
  const f = await fixture(t),
    created = await f.create(),
    object_id = created.body.data.projection.object_ref.object_id;
  assert.equal(
    (
      await f.invoke(
        "edit",
        {
          object_id,
          expected_revision: 1,
          command: { title: "他人编辑", body: "" },
        },
        "reviewer",
      )
    ).status,
    403,
  );
  for (const name of ["constructor", "toString", "__proto__"]) {
    assert.equal(
      (
        await f.invoke("submit", {
          object_id,
          expected_revision: 1,
          command: JSON.parse(`{"${name}":3}`),
        })
      ).status,
      422,
    );
  }
  const submitted = await f.invoke("submit", {
    object_id,
    expected_revision: 1,
    command: {},
  });
  assert.equal(submitted.body.data.projection.state, "submitted");
  assertTransition(
    moduleGraph,
    `${M}.record-lifecycle`,
    created.body.data.projection,
    "submit",
    submitted.body.data.projection,
  );
  const atom = moduleGraph.atoms.find(
    (item) => item.capability?.id === `${M}.submit`,
  );
  assertAtomResult(
    moduleGraph,
    atom.id,
    { object_id, expected_revision: 1, command: {} },
    submitted.body.data,
    (schema, value) => assert.ok(compile(schema)(value)),
    manifest,
  );
  assert.equal(
    (
      await f.invoke("approve", {
        object_id,
        expected_revision: 2,
        command: { note: "自审" },
      })
    ).status,
    403,
  );
  const approved = await f.invoke(
    "approve",
    { object_id, expected_revision: 2, command: { note: "合成审核通过" } },
    "reviewer",
  );
  assert.equal(approved.status, 200);
  assert.equal(approved.body.data.projection.revision, 3);
  assert.equal(approved.body.data.projection.state, "approved");
  assertTransition(
    moduleGraph,
    `${M}.record-lifecycle`,
    submitted.body.data.projection,
    "approve",
    approved.body.data.projection,
  );
  assert.throws(() =>
    assertTransition(
      moduleGraph,
      `${M}.record-lifecycle`,
      created.body.data.projection,
      "approve",
      approved.body.data.projection,
    ),
  );
  assert.equal(
    (
      await f.invoke("edit", {
        object_id,
        expected_revision: 3,
        command: { title: "不应修改", body: "" },
      })
    ).status,
    409,
  );
  await f.restart();
  const read = await f.flow(
    "objects.read",
    { object_ref: { object_type: M + ".record", object_id } },
    "reviewer",
  );
  assert.equal(read.body.data.projection.data.review_note, "合成审核通过");
});
test("atomic idempotency survives restart, reauthorizes replay, and never persists tickets", async (t) => {
  const f = await fixture(t),
    body = f.makeBody("create", {
      command: { project_id: "P", title: "幂等", body: "" },
    });
  const first = await f.request(body);
  await f.restart();
  const replay = await f.request(body);
  assert.deepEqual(replay.body, first.body);
  const conflict = structuredClone(body);
  conflict.input.command.title = "改变";
  assert.equal((await f.request(conflict)).status, 409);
  const database = new DatabaseSync(f.filename, { readOnly: true });
  assert.equal(
    database.prepare("SELECT count(*) AS n FROM records").get().n,
    1,
  );
  const receipts = database.prepare("SELECT * FROM kit_receipts").all();
  database.close();
  assert.ok(!JSON.stringify(receipts).includes(first.ticket));
  assert.ok(!readFileSync(f.filename).includes(Buffer.from(first.ticket)));
  f.revoked.add("author");
  assert.equal((await f.request(body)).status, 403);
});
test("concurrent same-revision edits commit one change and one receipt", async (t) => {
  const f = await fixture(t),
    created = await f.create(),
    object_id = created.body.data.projection.object_ref.object_id;
  const result = await Promise.all(
    ["一", "二"].map((title) =>
      f.invoke("edit", {
        object_id,
        expected_revision: 1,
        command: { title, body: "" },
      }),
    ),
  );
  assert.deepEqual(result.map((item) => item.status).sort(), [200, 409]);
  const read = await f.invoke("read", { object_id });
  assert.equal(read.body.data.projection.revision, 2);
});
test("revocation at the commit check rolls back object and receipt together", async (t) => {
  const f = await fixture(t);
  f.setDenyCommit(true);
  assert.equal((await f.create()).status, 403);
  f.setDenyCommit(false);
  assert.deepEqual((await f.flow("objects.list", {})).body.data.objects, []);
  const database = new DatabaseSync(f.filename, { readOnly: true });
  assert.equal(
    database.prepare("SELECT count(*) AS n FROM kit_receipts").get().n,
    0,
  );
  database.close();
});
test("read and list are scoped before pagination, and cursors bind actor and revision snapshot", async (t) => {
  const f = await fixture(t);
  for (let n = 0; n < 3; n++)
    assert.equal((await f.create("记录" + n)).status, 200);
  const first = await f.flow("objects.list", { limit: 2 });
  assert.equal(first.body.data.objects.length, 2);
  assert.ok(first.body.data.next_cursor);
  const cursor = first.body.data.next_cursor,
    object = first.body.data.objects[0];
  assert.equal(
    (await f.flow("objects.list", { limit: 2, cursor })).body.data.objects
      .length,
    1,
  );
  assert.equal(
    (await f.flow("objects.list", { limit: 2, cursor }, "reviewer")).status,
    409,
  );
  assert.deepEqual(
    (await f.flow("objects.list", {}, "outsider")).body.data.objects,
    [],
  );
  assert.equal(
    (
      await f.invoke(
        "read",
        { object_id: object.object_ref.object_id },
        "outsider",
      )
    ).status,
    403,
  );
  assert.equal(
    (await f.flow("objects.list", { limit: 2, cursor: cursor + "bad" })).status,
    422,
  );
  await f.invoke("edit", {
    object_id: object.object_ref.object_id,
    expected_revision: 1,
    command: { title: "更新", body: "" },
  });
  assert.equal(
    (await f.flow("objects.list", { limit: 2, cursor })).status,
    409,
  );
  assert.ok(
    first.body.data.objects.every((item) => !Object.hasOwn(item.data, "body")),
  );
});
test("unsupported files/jobs are explicit 501, and unsupported schema keywords fail startup", async (t) => {
  const f = await fixture(t);
  for (const operation of ["objects.detail", "uploads.create", "jobs.status"])
    assert.equal((await f.flow(operation, {})).status, 501);
  assert.throws(() => compile({ type: "object", allOf: [] }), /Schema/);
  assert.throws(
    () =>
      compile({ type: "object", properties: { value: { $ref: "#/$defs/a" } } }),
    /Schema/,
  );
});
test("schema subset never interprets prototype names as validators or ignores nested extra-field schemas", () => {
  const closed = compile({
    type: "object",
    properties: {},
    additionalProperties: false,
  });
  for (const name of ["constructor", "toString", "__proto__"])
    assert.equal(closed(JSON.parse(`{"${name}":3}`)), false);
  assert.throws(
    () => compile({ type: "object", additionalProperties: { type: "string" } }),
    /additionalProperties/,
  );
  const explicit = compile(
    JSON.parse(
      '{"type":"object","properties":{"__proto__":{"type":"string"}},"additionalProperties":false}',
    ),
  );
  assert.equal(explicit(JSON.parse('{"__proto__":"data"}')), true);
  assert.equal(explicit(JSON.parse('{"__proto__":3}')), false);
  assert.equal(Object.prototype.polluted, undefined);
});

test("observation delivery retries stable identities and isolates collector failures", async () => {
  const batches = [];
  const observer = createObservationClient({
    callbackUrl: "http://127.0.0.1:9999/api/v1/runtime-observations/events",
    graph: moduleGraph,
    fetchImpl: async (url, options) => {
      batches.push(JSON.parse(options.body));
      throw new Error("offline");
    },
  });
  const session = observer.session({
    ticket: "a".repeat(64),
    requestId: "b".repeat(32),
    atomId: moduleGraph.atoms[0].id,
  });
  session.mark("started");
  const result = { value: 7 };
  session.mark("succeeded");
  await observer.flush();
  assert.deepEqual(result, { value: 7 });
  assert.deepEqual(session.status(), { sequence: 2, dropped: 2 });
  assert.equal(batches.length, 4);
  assert.ok(
    batches.every((batch) =>
      batch.events.every((event) => !Object.hasOwn(event, "input")),
    ),
  );
  const absent = observer.session({
    requestId: "c".repeat(32),
    atomId: moduleGraph.atoms[0].id,
  });
  absent.mark("started");
  await observer.flush();
  assert.equal(batches.length, 4);
});

test("stateless role-based tools execute without central tickets and still reject unauthorized actors", async (t) => {
  const cap = {
    ...manifest.capabilities[0],
    id: `${M}.echo`,
    kind: "query",
    input_schema: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
      additionalProperties: false,
    },
    output_schema: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
      additionalProperties: false,
    },
  };
  const m = { ...manifest, capabilities: [cap] };
  const r = {
    registration_version: "1.0",
    module_id: M,
    module_version: m.module.version,
    permissions: { mode: "capability_roles", definition: null },
    data_objects: [],
    business_chains: [],
    host_requirements: { workflows: [], interactions: [] },
    workflow_operations: [],
  };
  const server = createModuleServer({
    manifest: m,
    registration: r,
    token,
    handlers: {
      [cap.id]: ({ body }) => ({
        summary: "已完成",
        data: { text: body.input.text },
      }),
    },
  });
  const url = await listen(server);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const body = {
    protocol_version: "1.1",
    request_id: id(),
    capability_id: cap.id,
    capability_version: cap.version,
    input: { text: "合成文字" },
    context: { workspace_id: "test", actor: actor("member") },
  };
  async function send(value) {
    return fetch(url + "/platform/v1/invoke", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(value),
    });
  }
  const accepted = await send(body);
  assert.equal(accepted.status, 200);
  assert.equal((await accepted.json()).data.text, "合成文字");
  body.context.actor.role = "researcher";
  body.context.actor.roles = ["researcher"];
  assert.equal((await send(body)).status, 403);
});
