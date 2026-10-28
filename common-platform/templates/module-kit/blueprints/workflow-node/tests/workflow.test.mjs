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
import { CoordinationClient } from "../sdk/node/coordination.mjs";
import { createBusiness, stateMachine } from "../backend/business.mjs";
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
async function fixture(t, { coordinated = false, credentialOnly = false, coordinationHandler = null, incoming = null,
  coordinationDelayMilliseconds = 0, coordinationUnavailable = false, incomingReady = true } = {}) {
  const directory = mkdtempSync(path.join(tmpdir(), "module-kit-node-"));
  const tickets = new Map(),
    seen = [],
    revoked = new Set();
  const events = [], acknowledgements = [];
  let pulls = 0;
  let signalFirstEvent;
  const firstEvent = new Promise(resolve => { signalFirstEvent = resolve; });
  let commitGate = null, signalCommit = null;
  const registrationDocument = structuredClone(registration);
  if (coordinated) registrationDocument.coordination = {
    version: "1.0",
    machines: [{id:stateMachine.id, object_types:[`${M}.record`], states:stateMachine.states.map(state => state.id), public_states:[], approval_required:false}],
    emits: ["state.changed"],
    accepts: incoming ? [{source_module:"neighbor.data",kind:"state.changed",mode:"notify"}] : [],
  };
  let unavailable = false,
    denyCommit = false;
  const central = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const input = body ? JSON.parse(body) : {},
      claims = tickets.get(req.headers["x-platform-access"]);
    if (req.url === "/api/v1/coordination/events") {
      assert.equal(req.headers.authorization, "Bearer synthetic-coordination-module-token");
      signalFirstEvent();
      if (coordinationDelayMilliseconds) await new Promise(resolve => setTimeout(resolve, coordinationDelayMilliseconds));
      if (coordinationUnavailable) {
        res.writeHead(503,{"Content-Type":"application/json"}); res.end("{}"); return;
      }
      const duplicate = events.some(event => event.event_id === input.event_id);
      if (!duplicate) events.push(input);
      res.writeHead(200, {"Content-Type":"application/json"});
      res.end(JSON.stringify({accepted:true, duplicate, event_id:input.event_id}));
      return;
    }
    if (req.url.startsWith("/api/v1/coordination/deliveries?")) {
      pulls++;
      res.writeHead(200, {"Content-Type":"application/json"});
      res.end(JSON.stringify({version:"1.0",module_id:M,consumer_id:M+".worker",deliveries:incoming && incomingReady && !acknowledgements.length ? [incoming] : []}));
      return;
    }
    if (req.url.startsWith("/api/v1/coordination/deliveries/") && req.url.endsWith("/ack")) {
      acknowledgements.push(input);
      res.writeHead(200, {"Content-Type":"application/json"});
      res.end(JSON.stringify({acknowledged:true,duplicate:false,delivery_id:incoming.id}));
      return;
    }
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
    if (commitGate && input.permission === `${M}.edit` &&
        seen.filter(item => item.request_id === input.request_id).length > 1) {
      signalCommit(); await commitGate; commitGate = null;
    }
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
      registrationDocument,
      coordinationConfig: coordinated || credentialOnly ? {version:"1.0",module_id:M,url:centralUrl,token:"synthetic-coordination-module-token",interval_seconds:300} : null,
      coordinationHandler,
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
    events,
    acknowledgements,
    get pulls() { return pulls; },
    pump: () => service.coordination.pump(),
    firstEvent,
    enableIncoming: () => { incomingReady = true; },
    holdNextEditCommit: () => {
      let release, signal;
      commitGate = new Promise(resolve => { release=resolve; });
      const entered = new Promise(resolve => { signal=resolve; });
      signalCommit = signal;
      return { entered, release };
    },
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
        registrationDocument,
        coordinationConfig: coordinated || credentialOnly ? {version:"1.0",module_id:M,url:centralUrl,token:"synthetic-coordination-module-token",interval_seconds:300} : null,
        coordinationHandler,
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
      "coordination-1.0",
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
  assert.equal((await confirm({...offer,platform_features:offer.platform_features.filter(feature => feature !== "coordination-1.0")})).status, 422);
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

test("optional coordination emits each committed business revision with trusted scope and no publication inference", async t => {
  const f = await fixture(t, { coordinated:true });
  const body = f.makeBody("create", {command:{project_id:"P",title:"协同记录",body:"内容"}});
  const created = await f.request(body), object_id = created.body.data.projection.object_ref.object_id;
  assert.equal(created.status, 200);
  assert.equal((await f.request(body)).status, 200);
  assert.equal((await f.invoke("edit", {object_id,expected_revision:1,command:{title:"修改",body:"已改"}})).status, 200);
  assert.equal((await f.invoke("submit", {object_id,expected_revision:2,command:{}})).status, 200);
  assert.equal((await f.invoke("approve", {object_id,expected_revision:3,command:{note:"审核通过"}}, "reviewer")).status, 200);
  for (let index=0;index<4;index++) await f.pump();
  assert.deepEqual(f.events.map(event => event.revision), [1,2,3,4]);
  assert.deepEqual(f.events.map(event => event.state), ["draft","draft","submitted","approved"]);
  assert.ok(f.events.every(event => event.kind === "state.changed" && event.scope.organization_id === "O" && event.scope.project_id === "P"));
  assert.equal(f.pulls, 0); // No handler means this module never claims messages.
  const db = new DatabaseSync(f.filename, {readOnly:true});
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM kit_receipts").get().n, 4);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_outbox").get().n, 4);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_outbox WHERE sent=1").get().n, 4);
  db.close();
  await f.restart();
  await f.pump();
  assert.equal(f.events.length, 4);
});

test("empty coordination declarations remain inactive even when a credential file is configured", async t => {
  const f = await fixture(t,{credentialOnly:true});
  assert.equal((await f.create()).status,200);
  assert.deepEqual(await f.pump(),{enabled:false});
  assert.equal(f.events.length,0);
  assert.equal(f.pulls,0);
  const db = new DatabaseSync(f.filename,{readOnly:true});
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='duoflow_coord_outbox'").get().n,0);
  db.close();
});

test("business, outbox and receipt roll back together after an enqueue failure", async t => {
  const directory = mkdtempSync(path.join(tmpdir(), "module-kit-coordination-"));
  const filename = path.join(directory, "records.sqlite");
  const c = new CoordinationClient({baseUrl:"http://127.0.0.1:19090/api/v1/coordination",token:"synthetic-coordination-module-token",moduleId:M,consumerId:M+".worker",scopes:[{organization_id:"O",project_id:"P"}],fetchImpl:async()=>{ throw Error("No network in the business transaction"); }});
  const original = c.enqueue.bind(c);
  c.enqueue = (db, event) => { original(db,event); throw Error("synthetic failure after outbox insertion"); };
  const business = createBusiness({filename,projects,decide:async()=>({allowed:true,policy_revision:1}),coordination:{
    client:c,declaration:{version:"1.0",machines:[{id:stateMachine.id,object_types:[`${M}.record`],states:stateMachine.states.map(state=>state.id),public_states:[],approval_required:false}],emits:["state.changed"],accepts:[]},
  }});
  t.after(async()=>{ await business.close(); rmSync(directory,{recursive:true,force:true}); });
  await assert.rejects(business.invoke({body:{protocol_version:"1.1",request_id:id(),capability_id:M+".create",capability_version:"1.0.0",input:{command:{project_id:"P",title:"原子保存",body:""}},context:{actor:actor("author")}},ticket:"synthetic-ticket"}), /synthetic failure after outbox insertion/);
  const db = new DatabaseSync(filename,{readOnly:true});
  for (const table of ["records","kit_receipts","duoflow_coord_outbox"])
    assert.equal(db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n, 0);
  db.close();
});

test("coordination pump uses the same business database and only explicit handlers apply received messages", async t => {
  const incoming = {id:"incoming-delivery",subscription_id:"neighbor-notify",target_module:M,mode:"notify",consumer_id:M+".worker",lease_token:"synthetic-delivery-lease-token",lease_until:9999999999,attempts:1,
    event:{version:"1.0",event_id:"neighbor-event",module_id:"neighbor.data",kind:"state.changed",machine_id:"neighbor.data.catalog",object_ref:{object_type:"neighbor.data.record",object_id:"neighbor-object"},state:"available",revision:3,scope:{organization_id:"O",project_id:"P"}}};
  let effects = 0;
  const f = await fixture(t,{coordinated:true,incoming,coordinationHandler:(db,event)=>{
    assert.equal(db.isTransaction,true);
    db.exec("CREATE TABLE IF NOT EXISTS neighbor_hints(object_id TEXT PRIMARY KEY,revision INTEGER)");
    db.prepare("INSERT INTO neighbor_hints VALUES (?,?)").run(event.object_ref.object_id,event.revision);
    effects++;
  }});
  const created = await f.create(), object_id = created.body.data.projection.object_ref.object_id;
  const replies = await Promise.all([f.pump(),f.invoke("edit",{object_id,expected_revision:1,command:{title:"并行编辑甲",body:""}}),f.invoke("edit",{object_id,expected_revision:1,command:{title:"并行编辑乙",body:""}})]);
  assert.deepEqual(replies.slice(1).map(reply=>reply.status).sort(),[200,409]);
  await f.pump();
  assert.equal(effects,1);
  assert.equal(f.acknowledgements.length,1);
  const db = new DatabaseSync(f.filename,{readOnly:true});
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_inbox").get().n,1);
  assert.equal(db.prepare("SELECT revision FROM neighbor_hints").get().revision,3);
  assert.equal(db.prepare("SELECT revision FROM records").get().revision,2);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_outbox").get().n,2);
  db.close();
});

for (const unavailable of [false,true]) test(`coordination ${unavailable ? "unavailability" : "slow HTTP"} leaves ordinary reads and edits responsive`, async t => {
  const f = await fixture(t,{coordinated:true,coordinationDelayMilliseconds:800,coordinationUnavailable:unavailable});
  let object_id;
  for (let index=0;index<5;index++) {
    const response = await f.create("并行对象"+index);
    assert.equal(response.status,200);
    object_id ??= response.body.data.projection.object_ref.object_id;
  }
  const pumping=f.pump();
  await f.firstEvent;
  const started=performance.now();
  const replies=await Promise.all([f.invoke("read",{object_id}),f.invoke("edit",{object_id,expected_revision:1,command:{title:"继续办理",body:""}})]);
  const elapsed=performance.now()-started;
  assert.deepEqual(replies.map(reply=>reply.status),[200,200]);
  assert.ok(elapsed<700,`ordinary requests waited ${Math.round(elapsed)}ms for the coordination network`);
  const result=await pumping;
  assert.equal(result.relay[unavailable?"failed":"sent"],5);
  const db=new DatabaseSync(f.filename,{readOnly:true});
  assert.equal(db.prepare("SELECT revision FROM records WHERE id=?").get(object_id).revision,2);
  if(unavailable) assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_outbox WHERE sent=0").get().n,6);
  db.close();
});

test("a business writer defers coordination consumption without waiting, then inbox and effect commit together",async t=>{
  const incoming={id:"deferred-delivery",subscription_id:"neighbor-notify",target_module:M,mode:"notify",consumer_id:M+".worker",lease_token:"synthetic-delivery-lease-token",lease_until:9999999999,attempts:1,
    event:{version:"1.0",event_id:"deferred-event",module_id:"neighbor.data",kind:"state.changed",machine_id:"neighbor.data.catalog",object_ref:{object_type:"neighbor.data.record",object_id:"neighbor-object"},state:"available",revision:3,scope:{organization_id:"O",project_id:"P"}}};
  let effects=0, handlerFile;
  const f=await fixture(t,{coordinated:true,incoming,incomingReady:false,coordinationHandler:(db,event)=>{
    handlerFile=db.prepare("PRAGMA database_list").all().find(database=>database.name==="main").file;
    db.exec("CREATE TABLE IF NOT EXISTS consumed_hints(object_id TEXT PRIMARY KEY,revision INTEGER)");
    db.prepare("INSERT INTO consumed_hints VALUES (?,?)").run(event.object_ref.object_id,event.revision);
    effects++;
  }});
  const created=await f.create(),object_id=created.body.data.projection.object_ref.object_id;
  await f.pump();
  const gate=f.holdNextEditCommit();
  const editing=f.invoke("edit",{object_id,expected_revision:1,command:{title:"保持写事务",body:""}});
  try {
    await gate.entered;
    f.enableIncoming();
    const started=performance.now(),result=await f.pump();
    assert.ok(performance.now()-started<700,"consumer waited for the business database write lock");
    assert.equal(result.failed,1);
    assert.equal(effects,0);
    assert.equal(f.acknowledgements.length,0);
    const db=new DatabaseSync(f.filename,{readOnly:true});
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_inbox").get().n,0);db.close();
  } finally { gate.release(); }
  assert.equal((await editing).status,200);
  await f.pump();
  assert.equal(effects,1);assert.equal(f.acknowledgements.length,1);
  assert.equal(path.resolve(handlerFile),path.resolve(f.filename));
  const db=new DatabaseSync(f.filename,{readOnly:true});
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM duoflow_coord_inbox").get().n,1);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM consumed_hints").get().n,1);db.close();
});
