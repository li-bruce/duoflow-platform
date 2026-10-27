/** Business changes belong here. SDK owns transport/transactions; the platform owns identity and grants. */
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Store } from "../sdk/node/store.mjs";
import { compile, requireThat, fail } from "../sdk/node/runtime.mjs";

export const MODULE_ID = "__MODULE_ID__";
export const OBJECT_TYPE = `${MODULE_ID}.record`;
// The same installed declaration drives visualization and authoritative state
// changes. It is static trusted module code, never a URL or a user expression.
export const stateMachine = JSON.parse(
  readFileSync(new URL("../frontend/flow.json", import.meta.url), "utf8"),
).flows[0].state_machine;
function transitionTarget(state, action) {
  const choices = stateMachine.transitions.filter(
    (item) => item.from === state && item.action_id === action,
  );
  requireThat(
    choices.length === 1,
    "state_conflict",
    "当前状态不允许此操作。",
    409,
  );
  return choices[0].to;
}
const identity = {
  type: "string",
  pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$",
};
const projectCheck = compile({
  type: "array",
  minItems: 1,
  maxItems: 100,
  items: {
    type: "object",
    required: ["project_id", "organization_id", "title"],
    properties: {
      project_id: identity,
      organization_id: identity,
      title: { type: "string", minLength: 1, maxLength: 160 },
    },
    additionalProperties: false,
  },
});
const listCheck = compile({
  type: "object",
  additionalProperties: false,
  properties: {
    object_type: { type: "string", const: OBJECT_TYPE },
    cursor: { type: "string", minLength: 1, maxLength: 2048 },
    limit: { type: "integer", minimum: 1, maximum: 50 },
  },
});
const readCheck = compile({
  type: "object",
  additionalProperties: false,
  required: ["object_ref"],
  properties: {
    object_ref: {
      type: "object",
      additionalProperties: false,
      required: ["object_type", "object_id"],
      properties: {
        object_type: { type: "string", const: OBJECT_TYPE },
        object_id: {
          type: "string",
          pattern: "^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$",
        },
      },
    },
  },
});
const ordinaryDenials = new Set([
  "grant_scope_mismatch",
  "actor_relation_mismatch",
  "separation_of_duties",
  "fact_condition_failed",
]);
export function createBusiness({ filename, projects, decide, ai = null }) {
  // Optional injected AIServiceManager. The neutral workflow below has no AI
  // action; a real module explicitly declares and calls its own AI capability.
  requireThat(
    projectCheck(projects) &&
      new Set(projects.map((item) => item.project_id)).size === projects.length,
    "project_config",
    "项目仓库配置无效或包含重复编号。",
    500,
  );
  // This trusted deployment configuration is the project repository for the reference package.
  // An existing service replaces this lookup with its own repository, never input.organization_id.
  const repository = new Map(
    projects.map((item) => [item.project_id, Object.freeze({ ...item })]),
  );
  const store = new Store(filename);
  store.db.exec(`CREATE TABLE IF NOT EXISTS records(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, organization_id TEXT NOT NULL, author_id TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
    reviewer_id TEXT, review_note TEXT, updated_at TEXT NOT NULL);`);
  function facts(record) {
    return {
      project_id: record.project_id,
      organization_id: record.organization_id,
      author_id: record.author_id,
    };
  }
  function load(id) {
    const record = store.db.prepare("SELECT * FROM records WHERE id=?").get(id);
    requireThat(
      record &&
        repository.get(record.project_id)?.organization_id ===
          record.organization_id,
      "object_scope",
      "记录不存在或项目配置已变化。",
      404,
    );
    return record;
  }
  async function decision(
    body,
    ticket,
    operation,
    permission,
    trustedFacts,
    optional = false,
  ) {
    const result = await decide({
      ticket,
      requestId: body.request_id,
      operation,
      permission: `${MODULE_ID}.${permission}`,
      facts: trustedFacts,
    });
    requireThat(
      result &&
        typeof result.allowed === "boolean" &&
        Number.isSafeInteger(result.policy_revision),
      "access_denied",
      "中央授权不可用。",
      403,
    );
    if (result.allowed) return result;
    if (
      optional &&
      ordinaryDenials.has(result.reason) &&
      result.policy_revision > 0
    )
      return result;
    throw fail("access_denied", "当前授权不允许此操作，请重新核验授权。", 403);
  }
  function projection(record, actor, summary = false) {
    const own = record.author_id === actor.id;
    const actions = stateMachine.transitions
      .filter(
        (item) =>
          item.from === record.state &&
          (item.action_id === "approve" ? !own : own),
      )
      .map((item) => item.action_id);
    return {
      object_ref: { object_type: OBJECT_TYPE, object_id: record.id },
      revision: record.revision,
      state: record.state,
      data: summary
        ? {
            title: record.title,
            project_id: record.project_id,
            organization_id: record.organization_id,
          }
        : {
            title: record.title,
            body: record.body,
            project_id: record.project_id,
            organization_id: record.organization_id,
            author_id: record.author_id,
            reviewer_id: record.reviewer_id,
            review_note: record.review_note,
          },
      allowed_actions: actions,
      jobs: [],
      updated_at: record.updated_at,
    };
  }
  async function list(body, ticket, operation, payload) {
    requireThat(listCheck(payload), "input_contract", "目录参数无效。");
    let policyRevision;
    const accepted = [];
    // Even an empty database checks a live ticket against configured trusted project facts.
    for (const project of repository.values()) {
      const result = await decision(
        body,
        ticket,
        operation,
        "read",
        {
          project_id: project.project_id,
          organization_id: project.organization_id,
        },
        true,
      );
      requireThat(
        policyRevision === undefined ||
          policyRevision === result.policy_revision,
        "access_denied",
        "读取期间权限定义已变化。",
        403,
      );
      policyRevision = result.policy_revision;
    }
    // Small-reference policy: at most 1000 records. Replace this with repository scope queries at scale.
    // Crucially, authorization happens before pagination and no unauthorized totals are returned.
    for (const record of store.db
      .prepare("SELECT * FROM records ORDER BY id")
      .all()) {
      if (
        repository.get(record.project_id)?.organization_id !==
        record.organization_id
      )
        continue;
      const result = await decision(
        body,
        ticket,
        operation,
        "read",
        facts(record),
        true,
      );
      requireThat(
        policyRevision === result.policy_revision,
        "access_denied",
        "读取期间权限定义已变化。",
        403,
      );
      if (result.allowed) accepted.push(record);
    }
    const binding = {
      module: MODULE_ID,
      actor: body.context.actor,
      policyRevision,
      snapshot: store.fingerprint(
        accepted.map((record) => [record.id, record.revision]),
      ),
    };
    const offset = store.offset(payload.cursor, binding),
      limit = payload.limit || 20;
    requireThat(
      offset <= accepted.length,
      "cursor_invalid",
      "分页游标超出目录范围。",
    );
    return {
      objects: accepted
        .slice(offset, offset + limit)
        .map((record) => projection(record, body.context.actor, true)),
      next_cursor:
        offset + limit < accepted.length
          ? store.cursor(binding, offset + limit)
          : null,
    };
  }
  async function invoke({ body, ticket }) {
    return store.transaction(async () => {
      const action = body.capability_id.slice(MODULE_ID.length + 1);
      requireThat(
        body.capability_id === `${MODULE_ID}.${action}` &&
          ["create", "edit", "submit", "approve", "read", "list"].includes(
            action,
          ),
        "capability_unknown",
        "未声明的能力。",
      );
      if (action === "list")
        return {
          summary: "已读取授权范围内的项目记录。",
          data: await list(body, ticket, "invoke", body.input),
        };
      if (action === "read") {
        const record = load(body.input.object_id);
        await decision(body, ticket, "invoke", "read", facts(record));
        return {
          summary: "已读取当前记录。",
          data: { projection: projection(record, body.context.actor) },
        };
      }
      const cached = store.receipt(body);
      const record = cached
        ? load(cached.objectId)
        : action === "create"
          ? null
          : load(body.input.object_id);
      const project =
        action === "create"
          ? repository.get(body.input.command.project_id)
          : null;
      requireThat(
        action !== "create" || project,
        "project_unknown",
        "所选项目不在服务端项目仓库。",
      );
      const trustedFacts = record
        ? facts(record)
        : { ...project, author_id: body.context.actor.id };
      delete trustedFacts.title;
      await decision(body, ticket, "invoke", action, trustedFacts);
      if (cached) return cached.response; // Reauthorize every retry, including after restart; never replay a write.
      const stamp = new Date().toISOString();
      let id = record?.id;
      if (action === "create") {
        requireThat(
          store.db.prepare("SELECT count(*) AS n FROM records").get().n < 1000,
          "capacity_limit",
          "参考包最多保存 1000 条记录，请扩展仓储后继续。",
          413,
        );
        requireThat(
          body.input.command.title.trim().length > 0,
          "title_required",
          "请输入标题。",
        );
        id = randomUUID();
        store.db
          .prepare("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?)")
          .run(
            id,
            project.project_id,
            project.organization_id,
            body.context.actor.id,
            body.input.command.title,
            body.input.command.body,
            stateMachine.initial,
            1,
            null,
            null,
            stamp,
          );
      } else {
        requireThat(
          record.revision === body.input.expected_revision,
          "revision_conflict",
          "记录已更新，请重新读取。",
          409,
        );
        const own = record.author_id === body.context.actor.id;
        requireThat(
          action === "approve" ? !own : own,
          "separation_of_duties",
          "当前办理人与记录作者的关系不符合要求。",
          403,
        );
        const nextState = transitionTarget(record.state, action);
        if (action === "edit")
          requireThat(
            body.input.command.title.trim().length > 0,
            "title_required",
            "请输入标题。",
          );
        if (action === "approve")
          requireThat(
            body.input.command.note.trim().length > 0,
            "note_required",
            "请输入审核意见。",
          );
        const changed = store.db
          .prepare(
            "UPDATE records SET title=?,body=?,state=?,revision=revision+1,reviewer_id=?,review_note=?,updated_at=? WHERE id=? AND revision=? AND state=?",
          )
          .run(
            action === "edit" ? body.input.command.title : record.title,
            action === "edit" ? body.input.command.body : record.body,
            nextState,
            action === "approve" ? body.context.actor.id : null,
            action === "approve" ? body.input.command.note : null,
            stamp,
            id,
            body.input.expected_revision,
            record.state,
          );
        requireThat(
          changed.changes === 1,
          "revision_conflict",
          "另一请求已修改记录。",
          409,
        );
      }
      // A final fresh central check precedes the atomic commit. Denial rolls back both object and receipt.
      await decision(body, ticket, "invoke", action, trustedFacts);
      const response = {
        summary: {
          create: "已创建草稿。",
          edit: "已保存记录。",
          submit: "已提交，等待其他审核人办理。",
          approve: "已审核通过。",
        }[action],
        data: { projection: projection(load(id), body.context.actor) },
      };
      store.saveReceipt(body, id, response);
      return response;
    });
  }
  async function workflow({ body, operation, ticket }) {
    return store.transaction(async () => {
      if (operation === "objects.list")
        return list(body, ticket, operation, body.input);
      requireThat(
        operation === "objects.read",
        "workflow_unsupported",
        "此参考包未实现明细分页、文件或长任务适配。",
        501,
      );
      requireThat(
        readCheck(body.input),
        "input_contract",
        "对象引用不符合契约。",
      );
      const record = load(body.input.object_ref.object_id);
      await decision(body, ticket, operation, "read", facts(record));
      return { projection: projection(record, body.context.actor) };
    });
  }
  // The transport dispatches through these exact maps and validates complete
  // manifest coverage before registration; the handshake never calls them.
  const handlers = Object.fromEntries(
    ["create", "edit", "submit", "approve", "read", "list"].map((action) => [
      `${MODULE_ID}.${action}`,
      invoke,
    ]),
  );
  const workflowHandlers = {
    "objects.list": workflow,
    "objects.read": workflow,
  };
  return {
    invoke,
    workflow,
    handlers,
    workflowHandlers,
    close: () => store.close(),
  };
}
