import { useEffect, useId, useRef, useState } from "react";
import {
  Boxes,
  Code2,
  GitBranch,
  Pause,
  Play,
  RefreshCw,
  Search,
} from "lucide-react";
import { api, dateTime } from "./api";
import { isPlatformAdmin } from "./types";
import type { User } from "./types";
import { Empty, ErrorNotice, Loading, PrettyData, SectionHeading } from "./ui";
import FlowHost from "./workflows/FlowHost";
import type { WorkflowStateMachine } from "./workflows/sdk";
import "./devtools.css";

type Json = Record<string, any>;
type Diagnostic = {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  path?: string;
};
type Registered = {
  id: string;
  version: string;
  title: string;
  pack: string;
  module_ids: string[];
  definition: Json;
  registration: {
    declaration_loaded: boolean;
    pack_installed: boolean;
    source_present: boolean;
    backend_available: boolean;
    state_machine_declared?: boolean;
  };
  diagnostics: Diagnostic[];
};
type Capability = {
  id: string;
  version: string;
  module_id: string;
  title: string;
  description?: string;
  kind: string;
  execution: Json;
  presentation: Json;
  input_schema: Json;
  output_schema: Json;
  available: boolean;
  availability: Json;
  bindings: { workflows: string[]; interactions: string[] };
  diagnostics: Diagnostic[];
};
type Catalog = {
  generated_at: string;
  modules: {
    id: string;
    name: string;
    version: string;
    enabled: boolean;
    source: string;
  }[];
  capabilities: Capability[];
  workflows: Registered[];
  interactions: Registered[];
  summary: {
    capabilities: number;
    workflows: number;
    interactions: number;
    state_machines: number;
  };
};
type Instance = {
  id: string;
  flow_id: string;
  title: string;
  updated_at: string;
};
type Observation = {
  id: string;
  observed_at: string;
  cursor: string;
  revision: number;
  business_state: string | null;
  business_revision: number | null;
  allowed_actions: string[];
  execution_status: string;
  error_code: string | null;
};
type Snapshot = {
  instance_id: string;
  flow_id: string;
  flow_version: string;
  title: string;
  observed_at: string;
  cursor: string;
  revision: number;
  object_ref: { object_type: string; object_id: string } | null;
  business: {
    state: string | null;
    revision: number | null;
    allowed_actions: string[];
    known_state: boolean | null;
  };
  execution: {
    status: string;
    run_id: string | null;
    error_code: string | null;
  };
  state_machine: WorkflowStateMachine | null;
  history: Observation[];
  diagnostics: Diagnostic[];
  history_note: string;
};
type Validation = {
  valid: boolean;
  kind: string;
  id: string | null;
  diagnostics: Diagnostic[];
  replacement: {
    registered: boolean;
    current_version?: string;
    incoming_version?: string;
    changed: boolean;
  } | null;
  preview: Json | null;
};
// Build-time inventory only: never load a script or import a URL supplied by the API.
const flowBundles = import.meta.glob("../../flows/packs/*/steps.tsx");
const componentBundles = import.meta.glob(
  "../../components/packs/*/Component.tsx",
);
const bundlePresent = (pack: string, kind: "workflow" | "interaction") =>
  Object.keys(kind === "workflow" ? flowBundles : componentBundles).some(
    (path) =>
      path.endsWith(
        `/packs/${pack}/${kind === "workflow" ? "steps.tsx" : "Component.tsx"}`,
      ),
  );

function Diagnostics({ items }: { items: Diagnostic[] }) {
  return items.length ? (
    <ul className="dev-diagnostics" aria-label="声明诊断">
      {items.map((item, index) => (
        <li className={item.severity} key={`${item.code}-${index}`}>
          <strong>
            {{ error: "错误", warning: "提醒", info: "说明" }[item.severity]} ·{" "}
            {item.code}
          </strong>
          <span>{item.message}</span>
          {item.path && <code>{item.path}</code>}
        </li>
      ))}
    </ul>
  ) : (
    <p className="dev-success">
      本次检查未发现声明关联问题；运行授权与实际业务结果需另行验证。
    </p>
  );
}

/** Edges are declared business transitions, never inferred from page step order. */
export function StateMachineGraph({
  machine,
  currentState,
  allowedActions = [],
}: {
  machine: WorkflowStateMachine | null | undefined;
  currentState?: string | null;
  allowedActions?: string[];
}) {
  const [selected, setSelected] = useState("");
  const marker = useId().replace(/:/g, "");
  if (!machine)
    return (
      <div className="dev-missing">
        <GitBranch size={24} />
        <div>
          <strong>尚未声明业务状态机</strong>
          <p>
            这个流程仍可运行。页面步骤仅表示个人导航，不能据此推断业务状态或状态迁移。
          </p>
        </div>
      </div>
    );
  const columns = Math.min(machine.states.length, 3) || 1;
  const width = columns * 280 + 40;
  const rows = Math.ceil(machine.states.length / columns);
  const height = Math.max(180, rows * 164 + 40);
  const positions = new Map(
    machine.states.map((state, i) => [
      state.id,
      { x: 30 + (i % columns) * 280, y: 35 + Math.floor(i / columns) * 164 },
    ]),
  );
  const active =
    machine.states.find((state) => state.id === selected) ||
    machine.states.find((state) => state.id === currentState) ||
    machine.states[0];
  const currentKnown =
    !currentState || machine.states.some((state) => state.id === currentState);
  const choose = (id: string) => setSelected(id);
  return (
    <section className="dev-machine" aria-label="业务状态机">
      <div className="dev-inline-heading">
        <div>
          <h3>{machine.id}</h3>
          <small>
            声明版本 {machine.version} · 初始状态 {machine.initial}
          </small>
        </div>
        <span className="tag">
          {machine.states.length} 个状态 / {machine.transitions.length} 条迁移
        </span>
      </div>
      {!currentKnown && (
        <p className="notice warning">
          服务当前返回状态「{currentState}
          」，未包含在声明中。请检查服务实现与声明版本。
        </p>
      )}
      <div className="dev-graph-scroll">
        <svg
          className="dev-graph"
          viewBox={`0 0 ${width} ${height}`}
          style={{ minWidth: Math.min(width, 650) }}
          aria-label="声明的状态与迁移关系"
        >
          <defs>
            <marker
              id={marker}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {machine.transitions.map((edge, index) => {
            const a = positions.get(edge.from),
              b = positions.get(edge.to);
            if (!a || !b) return null;
            const same = edge.from === edge.to;
            const next =
              machine.states.findIndex((s) => s.id === edge.to) >
              machine.states.findIndex((s) => s.id === edge.from);
            const sy = a.y + 42;
            const ey = b.y + 42;
            const sx = next ? a.x + 220 : a.x;
            const ex = next ? b.x : b.x + 220;
            const turn = (index % 3) * 9;
            const path = same
              ? `M ${a.x + 55} ${a.y} C ${a.x + 10} ${a.y - 30} ${a.x + 205} ${a.y - 30} ${a.x + 170} ${a.y}`
              : `M ${sx} ${sy} C ${sx + (next ? 40 : -35)} ${sy + turn} ${ex + (next ? -40 : 35)} ${ey - turn} ${ex} ${ey}`;
            return (
              <path
                key={edge.id}
                d={path}
                className={edge.from === currentState ? "from-current" : ""}
                markerEnd={`url(#${marker})`}
              >
                <title>
                  {edge.title || edge.action_id}: {edge.from} → {edge.to}
                </title>
              </path>
            );
          })}
          {machine.states.map((state) => {
            const { x, y } = positions.get(state.id)!;
            return (
              <g
                key={state.id}
                transform={`translate(${x},${y})`}
                role="button"
                tabIndex={0}
                aria-label={`状态 ${state.title}（${state.id}）${state.id === currentState ? "，当前业务状态" : ""}`}
                aria-pressed={active?.id === state.id}
                className={`dev-state ${state.id === currentState ? "current" : ""} ${state.id === active?.id ? "selected" : ""} ${state.terminal ? "terminal" : ""}`}
                onClick={() => choose(state.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    choose(state.id);
                  }
                }}
              >
                <rect width="220" height="84" rx="12" />
                <text x="14" y="26" className="dev-state-label">
                  {state.title.length > 17
                    ? state.title.slice(0, 16) + "…"
                    : state.title}
                </text>
                <text x="14" y="47" className="dev-state-id">
                  {state.id.length > 27
                    ? state.id.slice(0, 26) + "…"
                    : state.id}
                </text>
                <text x="14" y="67" className="dev-state-hint">
                  {state.id === currentState
                    ? "● 当前业务状态"
                    : state.terminal
                      ? "结束状态"
                      : state.id === machine.initial
                        ? "初始状态"
                        : "可选择查看迁移"}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="dev-legend">
        <span className="current">绿色：当前正式状态</span>
        <span>虚线：结束状态</span>
        <span>外框：正在查看的节点</span>
      </p>
      {active && (
        <div className="dev-state-detail">
          <h4>
            节点：{active.title} <code>{active.id}</code>
          </h4>
          <p>
            此处展示声明；动作是否可执行，以服务当前返回的 allowed_actions
            和执行时权限检查为准。
          </p>
          <ul>
            {machine.transitions
              .filter(
                (edge) => edge.from === active.id || edge.to === active.id,
              )
              .map((edge) => (
                <li key={edge.id}>
                  <strong>{edge.title || edge.action_id}</strong>
                  <span>
                    <code>{edge.from}</code> → <code>{edge.to}</code>
                  </span>
                  <span>
                    步骤 / 动作：
                    <code>
                      {edge.step_id} / {edge.action_id}
                    </code>
                  </span>
                  {edge.guard_description && (
                    <span>条件说明：{edge.guard_description}</span>
                  )}
                  {currentState === edge.from && (
                    <small>
                      {allowedActions.includes(edge.action_id)
                        ? "服务允许此动作；仍需在正式组件中执行"
                        : "当前允许动作中未包含此动作"}
                    </small>
                  )}
                </li>
              ))}
          </ul>
          {!machine.transitions.some(
            (edge) => edge.from === active.id || edge.to === active.id,
          ) && <p>没有关联迁移。</p>}
        </div>
      )}
    </section>
  );
}

function RegistrationDetails({
  entry,
  kind,
  onPreview,
}: {
  entry: Registered;
  kind: "workflow" | "interaction";
  onPreview: (kind: "workflow" | "interaction", value: Json) => void;
}) {
  const declaration = entry.definition;
  const built = bundlePresent(entry.pack, kind);
  return (
    <article className="dev-detail">
      <div className="dev-inline-heading">
        <div>
          <h3>{entry.title}</h3>
          <code>
            {entry.id} · v{entry.version}
          </code>
        </div>
        <button
          className="button secondary"
          onClick={() => onPreview(kind, declaration)}
        >
          载入验证编辑器
        </button>
      </div>
      <p>{declaration.description}</p>
      <div className="dev-registration">
        <span>
          声明：{entry.registration.declaration_loaded ? "已加载" : "未加载"}
        </span>
        <span>
          安装清单：{entry.registration.pack_installed ? "已列入" : "未列入"}
        </span>
        <span>
          组件源码：{entry.registration.source_present ? "存在" : "缺失"}
        </span>
        <span>当前浏览器构建：{built ? "包含该组件包" : "未包含该组件包"}</span>
        <span>
          后端能力：{entry.registration.backend_available ? "可用" : "未就绪"}
        </span>
      </div>
      <p className="subtle">
        同一份受控组件供页面和对话使用。前端包存在不等于通过权限和业务验收。
      </p>
      <Diagnostics items={entry.diagnostics} />
      {kind === "workflow" ? (
        <>
          <h4>步骤、前端组件与后端动作</h4>
          <div className="dev-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>个人步骤</th>
                  <th>共享前端组件</th>
                  <th>后端能力</th>
                </tr>
              </thead>
              <tbody>
                {(declaration.steps || []).map((step: Json) => (
                  <tr key={step.id}>
                    <td>
                      {step.title}
                      <br />
                      <code>{step.id}</code>
                    </td>
                    <td>
                      <code>
                        {entry.pack} / {step.component}
                      </code>
                    </td>
                    <td>
                      {(step.actions || []).map((action: Json) => (
                        <div key={action.id}>
                          <strong>{action.title || action.id}</strong>
                          <br />
                          <code>
                            {action.capability_id || "导航动作，无后端能力"}
                          </code>
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <StateMachineGraph
            key={entry.id}
            machine={declaration.state_machine}
          />
        </>
      ) : (
        <>
          <h4>组件动作与后端能力</h4>
          <ul>
            {(declaration.actions || []).map((action: Json) => (
              <li key={action.id}>
                {action.title || action.id} →{" "}
                <code>{action.capability_id}</code>
              </li>
            ))}
          </ul>
          <p>
            前端入口：<code>components/packs/{entry.pack}/Component.tsx</code>
          </p>
        </>
      )}
      <details>
        <summary>查看完整声明 JSON</summary>
        <PrettyData data={declaration} />
      </details>
    </article>
  );
}

function RuntimeObserver({ instance }: { instance: Instance }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [host, setHost] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [surface, setSurface] = useState<"page" | "conversation">("page");
  useEffect(() => {
    if (paused) return;
    let active = true;
    let pending = false;
    let controller: AbortController | null = null;
    const read = async () => {
      if (!active || pending) return;
      pending = true;
      controller = new AbortController();
      setBusy(true);
      try {
        const value = await api<Snapshot>(
          `/devtools/instances/${encodeURIComponent(instance.id)}`,
          { signal: controller.signal },
        );
        if (active) {
          setSnapshot(value);
          setError(null);
        }
      } catch (failure) {
        if (active) {
          setSnapshot(undefined);
          setHost(false);
          setError(failure);
        }
      } finally {
        pending = false;
        if (active) setBusy(false);
      }
    };
    void read();
    const timer = window.setInterval(() => void read(), 2000);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [instance.id, paused, refresh]);
  return (
    <section className="dev-runtime" aria-label="实时流程观察">
      <div className="dev-inline-heading">
        <div>
          <h3>{instance.title}</h3>
          <code>{instance.id}</code>
        </div>
        <div className="dev-buttons">
          <button
            className="button secondary"
            onClick={() => {
              setPaused((v) => !v);
              setBusy(false);
            }}
          >
            {paused ? <Play size={15} /> : <Pause size={15} />}{" "}
            {paused ? "恢复观察" : "暂停观察"}
          </button>
          <button
            className="button secondary"
            onClick={() => {
              setPaused(false);
              setRefresh((v) => v + 1);
            }}
            disabled={busy && !paused}
          >
            <RefreshCw size={15} />
            立即刷新
          </button>
        </div>
      </div>
      <p className="dev-observation-time" role="status">
        {paused
          ? "观察已暂停，以下是最后一次快照。"
          : busy
            ? "正在重新授权并读取当前状态…"
            : "每 2 秒重新授权并读取。"}{" "}
        最近成功读取：{snapshot ? dateTime(snapshot.observed_at) : "尚无"}。
      </p>
      <ErrorNotice error={error} />
      {snapshot ? (
        <>
          <div className="dev-state-cards">
            <div>
              <small>正式业务状态 · 模块保存</small>
              <strong>{snapshot.business.state || "尚未关联业务对象"}</strong>
              <span>业务修订 {snapshot.business.revision ?? "—"}</span>
            </div>
            <div>
              <small>个人导航 · 主框架保存</small>
              <strong>{snapshot.cursor}</strong>
              <span>个人草稿修订 {snapshot.revision}</span>
            </div>
            <div>
              <small>本次调用 · 执行器保存</small>
              <strong>{snapshot.execution.status}</strong>
              <span>运行 ID {snapshot.execution.run_id || "尚无"}</span>
            </div>
          </div>
          <p className="notice info">
            切换页面步骤不推进正式业务；复制对话分支也不回滚业务对象。只有正式动作在服务中通过校验，才会改变业务状态。
          </p>
          <p>
            对象：
            <code>
              {snapshot.object_ref
                ? `${snapshot.object_ref.object_type} / ${snapshot.object_ref.object_id}`
                : "未选择"}
            </code>
          </p>
          <p>
            服务当前允许的动作：
            {snapshot.business.allowed_actions.length
              ? snapshot.business.allowed_actions.map((action) => (
                  <code className="dev-action" key={action}>
                    {action}
                  </code>
                ))
              : "无"}
          </p>
          {snapshot.execution.error_code && (
            <p className="notice warning">
              调用错误代码：{snapshot.execution.error_code}
            </p>
          )}
          <Diagnostics items={snapshot.diagnostics} />
          <StateMachineGraph
            machine={snapshot.state_machine}
            currentState={snapshot.business.state}
            allowedActions={snapshot.business.allowed_actions}
          />
          <div className="dev-operation">
            <div className="dev-inline-heading">
              <div>
                <h3>通过真实组件办理</h3>
                <p>
                  动作仍走原有身份、修订和幂等校验；调试台不提供直接修改状态的入口。
                </p>
              </div>
              <button
                className="button secondary"
                onClick={() => setHost((value) => !value)}
              >
                {host ? "收起操作组件" : "展开真实操作组件"}
              </button>
            </div>
            {host && (
              <>
                <label>
                  操作形态
                  <select
                    aria-label="操作形态"
                    value={surface}
                    onChange={(event) =>
                      setSurface(event.target.value as "page" | "conversation")
                    }
                  >
                    <option value="page">固定页面布局</option>
                    <option value="conversation">对话步骤布局</option>
                  </select>
                </label>
                <p className="subtle">
                  这里是同一个流程实例的组件宿主预览。正式对话及历史请使用固定页面中的“在对话中继续”。
                </p>
                <FlowHost
                  key={instance.id + surface}
                  instanceId={instance.id}
                  surface={surface}
                  onChanged={() => setRefresh((value) => value + 1)}
                />
              </>
            )}
          </div>
          <details className="dev-history" open>
            <summary>最近观察记录（{snapshot.history.length}）</summary>
            <p>
              {snapshot.history_note || "宿主观察历史，不等于完整业务事件。"}{" "}
              轮询间隔内的中间变化可能未被记录。
            </p>
            <div className="dev-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>观察时间</th>
                    <th>正式状态 / 修订</th>
                    <th>个人步骤 / 修订</th>
                    <th>调用状态</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.history.map((row) => (
                    <tr key={row.id}>
                      <td>{dateTime(row.observed_at)}</td>
                      <td>
                        {row.business_state || "—"} /{" "}
                        {row.business_revision ?? "—"}
                      </td>
                      <td>
                        {row.cursor} / {row.revision}
                      </td>
                      <td>
                        {row.execution_status}
                        {row.error_code && ` · ${row.error_code}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      ) : (
        !error && <Loading label="正在读取本人流程…" />
      )}
    </section>
  );
}

export default function DevTools({ user }: { user: User }) {
  if (!isPlatformAdmin(user))
    return (
      <Empty
        title="开发调试台由管理员使用"
        description="实例观察仍遵循本人所有权和当前业务授权。"
      />
    );
  const identity = JSON.stringify([
    user.id,
    user.role,
    [...(user.roles || [])].sort(),
    user.authorization_revision,
  ]);
  return <DeveloperWorkspace key={identity} />;
}

function DeveloperWorkspace() {
  const [catalog, setCatalog] = useState<Catalog>();
  const [instances, setInstances] = useState<Instance[]>([]);
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [moduleId, setModuleId] = useState("");
  const [query, setQuery] = useState("");
  const [section, setSection] = useState<
    "capability" | "workflow" | "interaction"
  >("workflow");
  const [selected, setSelected] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [validationKind, setValidationKind] = useState<
    "workflow" | "interaction" | "state_machine"
  >("workflow");
  const [json, setJson] = useState("");
  const [validation, setValidation] = useState<Validation>();
  const [validationError, setValidationError] = useState<unknown>();
  const [validating, setValidating] = useState(false);
  const validationEpoch = useRef(0);
  const validationAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void Promise.all([
      api<Catalog>("/devtools/catalog", { signal: controller.signal }),
      api<{ items: Instance[] }>("/devtools/instances", {
        signal: controller.signal,
      }),
    ])
      .then(([a, b]) => {
        if (active) {
          setCatalog(a);
          setInstances(b.items);
          setInstanceId((id) =>
            b.items.some((item) => item.id === id) ? id : "",
          );
        }
      })
      .catch((failure) => {
        if (active) {
          setCatalog(undefined);
          setInstances([]);
          setInstanceId("");
          setError(failure);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [revision]);
  useEffect(
    () => () => {
      validationEpoch.current++;
      validationAbort.current?.abort();
    },
    [],
  );
  function resetValidation() {
    validationEpoch.current++;
    validationAbort.current?.abort();
    setValidation(undefined);
    setValidationError(null);
    setValidating(false);
  }
  function preview(kind: "workflow" | "interaction", definition: Json) {
    resetValidation();
    setValidationKind(kind);
    setJson(JSON.stringify(definition, null, 2));
    document
      .getElementById("dev-validation")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function validate() {
    resetValidation();
    const epoch = validationEpoch.current;
    try {
      if (json.length > 1_000_000)
        throw new Error("声明超过 256 KiB，请缩小后重试。");
      const definition = JSON.parse(json);
      if (
        !definition ||
        typeof definition !== "object" ||
        Array.isArray(definition)
      )
        throw new Error("请提供单个 JSON 对象。");
      const body = JSON.stringify({ kind: validationKind, definition });
      if (new TextEncoder().encode(body).byteLength > 256 * 1024)
        throw new Error("声明超过 256 KiB，请缩小后重试。");
      setValidating(true);
      const controller = new AbortController();
      validationAbort.current = controller;
      const value = await api<Validation>("/devtools/validate", {
        method: "POST",
        body,
        signal: controller.signal,
      });
      if (epoch === validationEpoch.current) setValidation(value);
    } catch (failure) {
      if (epoch === validationEpoch.current) setValidationError(failure);
    } finally {
      if (epoch === validationEpoch.current) setValidating(false);
    }
  }
  const needle = query.trim().toLowerCase();
  const match = (item: { id: string; title: string }) =>
    !needle || `${item.id} ${item.title}`.toLowerCase().includes(needle);
  const caps = (catalog?.capabilities || []).filter(
    (item) => (!moduleId || item.module_id === moduleId) && match(item),
  );
  const flows = (catalog?.workflows || []).filter(
    (item) => (!moduleId || item.module_ids.includes(moduleId)) && match(item),
  );
  const interactions = (catalog?.interactions || []).filter(
    (item) => (!moduleId || item.module_ids.includes(moduleId)) && match(item),
  );
  const rows =
    section === "capability"
      ? caps
      : section === "workflow"
        ? flows
        : interactions;
  const chosen = rows.find((item) => item.id === selected) || rows[0];
  const current = instances.find((item) => item.id === instanceId);
  return (
    <div className="devtools">
      <SectionHeading
        eyebrow="FRAMEWORK DEVELOPER TOOLS"
        title="模块开发调试台"
        description="查看声明怎样连接，观察真实业务怎样运行。页面、对话和调试台共用同一套模块接口。"
        actions={
          <button
            className="button secondary"
            disabled={loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={16} />
            刷新目录与本人流程
          </button>
        }
      />
      <div className="notice info">
        管理员可以检查已加载的声明；运行观察只读取本人流程和当前获授权对象。这里不显示其他用户私人草稿，也不代替业务授权。
      </div>
      <ErrorNotice error={error} />
      {loading && !catalog && <Loading label="正在读取注册关系…" />}
      {catalog && (
        <>
          <div className="dev-statistics">
            <div>
              <Code2 size={20} />
              <strong>{catalog.summary.capabilities}</strong>
              <span>后端能力</span>
            </div>
            <div>
              <Boxes size={20} />
              <strong>{catalog.summary.interactions}</strong>
              <span>独立前端组件</span>
            </div>
            <div>
              <GitBranch size={20} />
              <strong>{catalog.summary.workflows}</strong>
              <span>步骤流程</span>
            </div>
            <div>
              <strong>{catalog.summary.state_machines}</strong>
              <span>已声明状态机</span>
            </div>
          </div>
          <section className="panel">
            <div className="dev-inline-heading">
              <div>
                <h2>1. 检查注册与组合</h2>
                <p>先选模块，再看前端组件、后端能力、流程声明的对应关系。</p>
              </div>
              <small>目录读取于 {dateTime(catalog.generated_at)}</small>
            </div>
            <div className="dev-filters">
              <label>
                模块
                <select
                  aria-label="调试模块"
                  value={moduleId}
                  onChange={(event) => {
                    setModuleId(event.target.value);
                    setSelected("");
                  }}
                >
                  <option value="">全部模块</option>
                  {catalog.modules.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.id}
                      {!item.enabled ? "（已停用）" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <Search size={14} /> 搜索
                <input
                  aria-label="搜索声明"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="名称或 ID"
                />
              </label>
            </div>
            <div className="dev-tabs" role="tablist" aria-label="注册声明类型">
              {(
                [
                  ["workflow", "流程与步骤组件", flows.length],
                  ["interaction", "独立前端组件", interactions.length],
                  ["capability", "后端能力", caps.length],
                ] as const
              ).map(([id, title, count]) => (
                <button
                  role="tab"
                  aria-selected={section === id}
                  key={id}
                  onClick={() => {
                    setSection(id);
                    setSelected("");
                  }}
                >
                  {title} <span>{count}</span>
                </button>
              ))}
            </div>
            <div className="dev-catalog-layout">
              <aside aria-label="声明目录">
                {rows.map((item) => (
                  <button
                    key={item.id}
                    className={item.id === chosen?.id ? "selected" : ""}
                    onClick={() => setSelected(item.id)}
                  >
                    <strong>{item.title}</strong>
                    <code>{item.id}</code>
                    <small>v{item.version}</small>
                  </button>
                ))}
              </aside>
              <div>
                {!chosen ? (
                  <Empty title="当前筛选没有匹配的声明" />
                ) : section === "capability" ? (
                  (() => {
                    const cap = chosen as Capability;
                    return (
                      <article className="dev-detail">
                        <h3>{cap.title}</h3>
                        <code>{cap.id}</code>
                        <p>{cap.description}</p>
                        <p>
                          所属模块：{cap.module_id} · 当前
                          {cap.available ? "可用" : "不可用"}
                        </p>
                        <p>
                          关联流程：{cap.bindings.workflows.join("、") || "无"}
                        </p>
                        <p>
                          关联组件：
                          {cap.bindings.interactions.join("、") || "无"}
                        </p>
                        <Diagnostics items={cap.diagnostics} />
                        <details open>
                          <summary>执行与呈现声明</summary>
                          <PrettyData
                            data={{
                              kind: cap.kind,
                              execution: cap.execution,
                              presentation: cap.presentation,
                              availability: cap.availability,
                            }}
                          />
                        </details>
                        <details>
                          <summary>输入 / 输出 Schema</summary>
                          <PrettyData
                            data={{
                              input: cap.input_schema,
                              output: cap.output_schema,
                            }}
                          />
                        </details>
                      </article>
                    );
                  })()
                ) : (
                  <RegistrationDetails
                    key={chosen.id}
                    entry={chosen as Registered}
                    kind={section}
                    onPreview={preview}
                  />
                )}
              </div>
            </div>
          </section>
        </>
      )}
      <section className="panel" id="dev-validation">
        <div className="dev-inline-heading">
          <div>
            <h2>2. 验证准备接入的声明</h2>
            <p>
              粘贴
              JSON，检查字段与注册关联。验证不会安装、发布或执行代码；正式安装仍需受控构建。
            </p>
          </div>
        </div>
        <label className="field">
          声明类型
          <select
            aria-label="验证声明类型"
            value={validationKind}
            onChange={(event) => {
              resetValidation();
              setValidationKind(event.target.value as typeof validationKind);
            }}
          >
            <option value="workflow">单个流程定义</option>
            <option value="interaction">独立交互组件</option>
            <option value="state_machine">业务状态机</option>
          </select>
        </label>
        <textarea
          className="dev-json-editor"
          aria-label="待验证声明 JSON"
          spellCheck={false}
          value={json}
          onChange={(event) => {
            resetValidation();
            setJson(event.target.value);
          }}
          placeholder="粘贴单个声明对象，或从上方“载入验证编辑器”开始。"
        />
        <button
          className="button primary"
          disabled={validating || !json.trim()}
          onClick={() => void validate()}
        >
          {validating ? "正在验证…" : "验证声明（只读）"}
        </button>
        <ErrorNotice error={validationError} />
        {validation && (
          <div className="dev-validation-result" role="status">
            <h3>{validation.valid ? "声明校验通过" : "声明需要修正"}</h3>
            {validation.replacement && (
              <p>
                {validation.replacement.registered
                  ? "当前已有同 ID 注册"
                  : "当前尚未注册此 ID"}
                {validation.replacement.changed
                  ? "；声明与已注册版本不同。"
                  : "。"}
                {validation.replacement.current_version &&
                  ` 当前版本 ${validation.replacement.current_version}，待检查版本 ${validation.replacement.incoming_version || "—"}。`}
              </p>
            )}
            <Diagnostics items={validation.diagnostics} />
            {validation.valid && validation.preview && (
              <>
                {validationKind !== "interaction" && (
                  <StateMachineGraph
                    machine={
                      validationKind === "state_machine"
                        ? (validation.preview as WorkflowStateMachine)
                        : validation.preview.state_machine
                    }
                  />
                )}
                <details>
                  <summary>校验后的声明预览</summary>
                  <PrettyData data={validation.preview} />
                </details>
              </>
            )}
          </div>
        )}
      </section>
      <section className="panel">
        <h2>3. 观察本人真实运行实例</h2>
        <p>
          先在“我的业务”或“步骤工作台”创建流程，再刷新列表并选择。列表中的实例属于当前账号。
        </p>
        <label className="field">
          选择个人流程
          <select
            aria-label="观察个人流程"
            value={instanceId}
            onChange={(event) => setInstanceId(event.target.value)}
          >
            <option value="">选择要观察的本人流程</option>
            {instances.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} · {item.id.slice(0, 8)} · {item.flow_id}
              </option>
            ))}
          </select>
        </label>
        {current ? (
          <RuntimeObserver key={current.id} instance={current} />
        ) : (
          <Empty
            title="选择一个本人流程，开始实时观察"
            description="没有实例时，可前往步骤工作台创建；管理员身份不自动获得业务办理权限。"
            action={
              <a className="button secondary" href="#flows">
                打开步骤工作台
              </a>
            }
          />
        )}
      </section>
    </div>
  );
}
