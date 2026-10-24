/** Portable module-kit transport v1. Server only; no login, RBAC or browser credentials. */
import http from "node:http";
import { timingSafeEqual } from "node:crypto";
import { createRegistration } from "./registration.mjs";
import { createAtomBindings } from "./atoms.mjs";

export function fail(code, message, status = 422) {
  return Object.assign(new Error(message), { code, status });
}
export function requireThat(condition, code, message, status = 422) {
  if (!condition) throw fail(code, message, status);
}
export const canonical = (value) => JSON.stringify(sort(value));
function sort(value) {
  if (Array.isArray(value)) return value.map(sort);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sort(value[key])]),
    );
  return value;
}
const plain = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const keywords = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "minItems",
  "maxItems",
  "uniqueItems",
  "minLength",
  "maxLength",
  "minimum",
  "maximum",
  "pattern",
  "enum",
  "const",
  "title",
  "description",
  "default",
  "$schema",
]);
/** Deliberately closed JSON Schema subset, not an implementation of arbitrary JSON Schema.
 * Unknown validation keywords fail at startup; install a full validator before expanding it. */
export function compile(schema) {
  requireThat(
    plain(schema) && Object.keys(schema).every((key) => keywords.has(key)),
    "schema_unsupported",
    "SDK 不支持此 Schema 关键字。",
    500,
  );
  requireThat(
    schema.additionalProperties === undefined ||
      typeof schema.additionalProperties === "boolean",
    "schema_unsupported",
    "SDK 的 additionalProperties 只支持布尔值。",
    500,
  );
  requireThat(
    schema.properties === undefined || plain(schema.properties),
    "schema_unsupported",
    "Schema properties 必须是对象。",
    500,
  );
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  requireThat(
    types.every((type) =>
      [
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
      ].includes(type),
    ),
    "schema_unsupported",
    "Schema 必须显式声明受支持类型。",
    500,
  );
  const children = Object.fromEntries(
    Object.entries(schema.properties || {}).map(([key, value]) => [
      key,
      compile(value),
    ]),
  );
  const child = schema.items ? compile(schema.items) : null;
  const pattern = schema.pattern ? new RegExp(schema.pattern, "u") : null;
  return (value) => {
    const type =
      value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
    if (
      !types.includes(type) &&
      !(types.includes("integer") && Number.isSafeInteger(value))
    )
      return false;
    if (
      typeof value === "number" &&
      (!Number.isFinite(value) ||
        (schema.minimum !== undefined && value < schema.minimum) ||
        (schema.maximum !== undefined && value > schema.maximum))
    )
      return false;
    if (
      Object.hasOwn(schema, "const") &&
      canonical(schema.const) !== canonical(value)
    )
      return false;
    if (
      schema.enum &&
      !schema.enum.some((item) => canonical(item) === canonical(value))
    )
      return false;
    if (
      typeof value === "string" &&
      ((schema.minLength !== undefined &&
        [...value].length < schema.minLength) ||
        (schema.maxLength !== undefined &&
          [...value].length > schema.maxLength) ||
        (pattern && !pattern.test(value)))
    )
      return false;
    if (
      Array.isArray(value) &&
      ((schema.minItems !== undefined && value.length < schema.minItems) ||
        (schema.maxItems !== undefined && value.length > schema.maxItems) ||
        (schema.uniqueItems &&
          new Set(value.map(canonical)).size !== value.length) ||
        (child && !value.every(child)))
    )
      return false;
    if (plain(value)) {
      if ((schema.required || []).some((key) => !Object.hasOwn(value, key)))
        return false;
      for (const [key, item] of Object.entries(value)) {
        // Only own properties are declared fields. constructor/toString must not
        // call Object.prototype functions as validators; __proto__ remains data.
        if (
          Object.hasOwn(children, key)
            ? !children[key](item)
            : schema.additionalProperties === false
        )
          return false;
      }
    }
    return true;
  };
}
export const objectSchema = (properties) => ({
  type: "object",
  properties,
  required: Object.keys(properties),
  additionalProperties: false,
});
const string = (maxLength = 100) => ({
  type: "string",
  minLength: 1,
  maxLength,
});
const actorProperties = {
  id: string(),
  role: string(40),
  roles: {
    type: "array",
    minItems: 1,
    maxItems: 20,
    uniqueItems: true,
    items: string(40),
  },
  organization_id: { type: ["string", "null"], maxLength: 100 },
  team_ids: {
    type: "array",
    maxItems: 100,
    uniqueItems: true,
    items: string(),
  },
  name: string(),
  authorization_revision: { type: "integer", minimum: 1 },
};
const actorSchema = objectSchema(actorProperties);
const requestId = { type: "string", pattern: "^[0-9a-f]{32}$" };
const workflowCheck = compile(
  objectSchema({
    protocol_version: { type: "string", const: "1.0" },
    request_id: requestId,
    context: objectSchema({ actor: actorSchema }),
    input: { type: "object" },
  }),
);
const invokeActor = {
  ...actorSchema,
  properties: {
    ...actorProperties,
    role: {
      ...string(40),
      enum: [
        "admin",
        "member",
        "researcher",
        "contributor",
        "curator",
        "reviewer",
        "system_admin",
      ],
    },
    roles: {
      ...actorProperties.roles,
      maxItems: 7,
      items: {
        ...string(40),
        enum: [
          "admin",
          "member",
          "researcher",
          "contributor",
          "curator",
          "reviewer",
          "system_admin",
        ],
      },
    },
  },
};
const invokeCheck = compile(
  objectSchema({
    protocol_version: { type: "string", const: "1.1" },
    request_id: requestId,
    capability_id: { type: "string", pattern: "^[a-z][a-z0-9_.-]{2,120}$" },
    capability_version: string(40),
    input: { type: "object" },
    context: objectSchema({ workspace_id: string(), actor: invokeActor }),
  }),
);
async function bodyOf(request, limit = 65536) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    requireThat(size <= limit, "request_size", "请求超过接口大小限制。", 413);
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw fail("invalid_json", "请求不是有效 JSON。");
  }
}
function send(response, status, data) {
  const body = JSON.stringify(data);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}
export function createModuleServer({
  manifest,
  registration,
  token,
  handlers,
  workflowHandlers = {},
  graph = null,
  observationClient = null,
}) {
  // Snapshot the same dispatch tables that acceptance checks. Caller mutation
  // after startup must not silently remove a previously confirmed interface.
  manifest = structuredClone(manifest);
  registration = structuredClone(registration);
  handlers = Object.freeze({ ...handlers });
  workflowHandlers = Object.freeze({ ...workflowHandlers });
  const atoms = graph
    ? createAtomBindings({ graph, manifest, handlers })
    : null;
  requireThat(
    typeof token === "string" && token.length >= 24,
    "service_config",
    "请配置至少 24 字符的私有服务凭据。",
    500,
  );
  const expected = Buffer.from(`Bearer ${token}`);
  const capabilities = new Map(
    manifest.capabilities.map((cap) => [
      cap.id,
      {
        cap,
        input: compile(cap.input_schema),
        output: compile(cap.output_schema),
      },
    ]),
  );
  const registrar = createRegistration({
    manifest,
    registration,
    handlers,
    workflowHandlers,
    compile,
    graph: atoms?.document,
    runtimeProbe: observationClient?.probe,
  });
  return http.createServer(async (request, response) => {
    let observation;
    try {
      const received = Buffer.from(request.headers.authorization || "");
      requireThat(
        received.length === expected.length &&
          timingSafeEqual(received, expected),
        "service_auth",
        "服务连接认证失败。",
        401,
      );
      if (request.method === "GET" && request.url === "/platform/v1/health")
        return send(response, 200, { status: "ok" });
      if (request.method === "GET" && request.url === "/platform/v1/manifest")
        return send(response, 200, manifest);
      if (
        atoms &&
        request.method === "GET" &&
        request.url === "/platform/v1/module-graph"
      )
        return send(response, 200, atoms.document);
      if (
        registration.flow_exports &&
        request.method === "GET" &&
        request.url === "/platform/v1/flow-exports"
      )
        return send(response, 200, registrar.document.flow_exports);
      if (
        request.method === "GET" &&
        request.url === "/platform/v1/registration"
      )
        return send(response, 200, registrar.document);
      if (request.method === "POST" && request.url === "/platform/v1/handshake")
        return send(
          response,
          200,
          await registrar.confirm(await bodyOf(request, 1048576)),
        );
      const operation = request.url?.startsWith("/platform/v1/workflow/")
        ? request.url.slice("/platform/v1/workflow/".length)
        : null;
      requireThat(
        request.method === "POST" &&
          (operation || request.url === "/platform/v1/invoke"),
        "not_found",
        "接口不存在。",
        404,
      );
      const body = await bodyOf(request);
      requireThat(
        (operation ? workflowCheck : invokeCheck)(body) &&
          body.context.actor.roles.includes(body.context.actor.role),
        "input_contract",
        "请求包络不符合契约。",
      );
      // Access tickets are ephemeral arguments only: never included in body fingerprints, responses or persistence.
      const ticket = request.headers["x-platform-access"];
      if (registration.permissions.mode === "platform_access") {
        requireThat(
          typeof ticket === "string" &&
            ticket.length > 0 &&
            ticket.length <= 12288,
          "access_denied",
          "缺少有效的中央授权票据。",
          403,
        );
      }
      if (operation) {
        requireThat(
          Object.hasOwn(workflowHandlers, operation),
          "workflow_unsupported",
          "此服务未注册该对象接口。",
          501,
        );
        const data = await workflowHandlers[operation]({
          body,
          operation,
          ticket,
        });
        requireThat(
          Buffer.byteLength(JSON.stringify(data)) <=
            (operation === "objects.list" ? 131072 : 1048576),
          "response_size",
          "模块响应超出契约上限。",
          500,
        );
        return send(response, 200, {
          protocol_version: "1.0",
          request_id: body.request_id,
          data,
        });
      }
      const spec = capabilities.get(body.capability_id);
      requireThat(
        spec &&
          spec.cap.version === body.capability_version &&
          spec.input(body.input),
        "input_contract",
        "能力、版本或参数不符合契约。",
      );
      if (registration.permissions.mode === "capability_roles") {
        requireThat(
          spec.cap.roles.some((role) =>
            body.context.actor.roles.includes(role),
          ),
          "access_denied",
          "当前身份无权使用此功能。",
          403,
        );
      }
      if (atoms && observationClient) {
        observation = observationClient.session({
          ticket: request.headers["x-duoflow-observation"],
          requestId: body.request_id,
          atomId: atoms.forCapability(body.capability_id).id,
        });
        observation.mark("started");
      }
      const output = await handlers[body.capability_id]({
        body,
        ticket,
        observe: observation,
      });
      requireThat(
        typeof output.summary === "string" &&
          output.summary.length <= 4000 &&
          spec.output(output.data) &&
          Buffer.byteLength(JSON.stringify(output)) <= 1048576,
        "output_contract",
        "模块输出不符合契约。",
        500,
      );
      observation?.mark("succeeded");
      return send(response, 200, {
        protocol_version: "1.1",
        request_id: body.request_id,
        capability_id: body.capability_id,
        capability_version: body.capability_version,
        summary: output.summary,
        data: output.data,
      });
    } catch (error) {
      observation?.mark("failed");
      const safe =
        Number.isInteger(error.status) &&
        error.status >= 400 &&
        error.status < 500;
      send(response, Number.isInteger(error.status) ? error.status : 500, {
        error: {
          code: error.code || "handler_failed",
          message: safe ? error.message : "服务暂时无法完成此操作。",
        },
      });
    }
  });
}
