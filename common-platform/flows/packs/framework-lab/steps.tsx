import { useRef, useState } from "react";
import type {
  WorkflowStepComponents,
  WorkflowStepProps,
} from "@platform/workflow-sdk";
import definition from "./flow.json";

const machine = definition.flows[0].state_machine;
const labels: Record<string, string> = Object.fromEntries(
  machine.states.map((state) => [state.id, state.title]),
);
const objectType = definition.flows[0].provider.object_type;
function useAction() {
  const [error, setError] = useState(""),
    [pending, setPending] = useState(false);
  const running = useRef(false);
  async function run(work: () => Promise<unknown>) {
    if (running.current) return;
    running.current = true;
    setPending(true);
    setError("");
    try {
      await work();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "操作未完成，请刷新后重试。",
      );
    } finally {
      running.current = false;
      setPending(false);
    }
  }
  return { error, pending, run };
}
function allowed(props: WorkflowStepProps, id: string) {
  return props.actions.some((action) => action.id === id && action.available);
}
function Status({ projection }: Pick<WorkflowStepProps, "projection">) {
  if (!projection) return null;
  return (
    <p aria-live="polite">
      正式状态：<strong>{labels[projection.state] ?? projection.state}</strong>{" "}
      · 业务修订 {projection.revision} · 工作项{" "}
      {projection.object_ref.object_id.slice(0, 8)}
    </p>
  );
}
function Picker(props: WorkflowStepProps) {
  const action = useAction(),
    locked = props.busy || action.pending;
  const title = String(props.draft.title ?? ""),
    body = String(props.draft.body ?? "");
  return (
    <section aria-label="本人工作项目录">
      <h3>本人工作项</h3>
      <p>这是单人调试工作台。管理员也只能看到自己的工作项。</p>
      <ul>
        {props.objects.map((item) => (
          <li key={item.object_ref.object_id}>
            <button
              type="button"
              disabled={locked}
              onClick={() =>
                void action.run(async () => {
                  await props.openObject(item.object_ref.object_id);
                  props.navigate(
                    item.state === "draft" || item.state === "returned"
                      ? "edit"
                      : "inspect",
                  );
                })
              }
            >
              {String(item.data.title)} · {labels[item.state] ?? item.state} ·
              修订 {item.revision}
            </button>
          </li>
        ))}
      </ul>
      {!props.objects.length && (
        <p>尚无工作项。填写下面的标题和说明即可开始。</p>
      )}
      {props.objectPages.some(
        (page) => page.object_type === objectType && page.next_cursor,
      ) && (
        <button
          type="button"
          disabled={locked}
          onClick={() =>
            void action.run(() => props.loadMoreObjects(objectType))
          }
        >
          加载更多工作项
        </button>
      )}
      <label>
        工作项标题
        <input
          aria-label="新工作项标题"
          value={title}
          maxLength={200}
          disabled={locked || !allowed(props, "create")}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, title: event.target.value })
          }
        />
      </label>
      <label>
        工作项说明
        <textarea
          aria-label="新工作项说明"
          value={body}
          maxLength={4000}
          disabled={locked || !allowed(props, "create")}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, body: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !allowed(props, "create") || !title.trim()}
        onClick={() =>
          void action.run(() => props.execute("create", { title, body }))
        }
      >
        创建工作项
      </button>
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
function Editor(props: WorkflowStepProps) {
  const action = useAction();
  if (!props.projection)
    return (
      <section>
        <p>先选择或创建一个工作项，再填写内容。</p>
        <button type="button" onClick={() => props.navigate("pick")}>
          返回工作项目录
        </button>
      </section>
    );
  const { data } = props.projection,
    locked = props.busy || action.pending;
  const title = String(props.draft.title ?? data.title ?? ""),
    body = String(props.draft.body ?? data.body ?? "");
  const changed =
    title !== String(data.title ?? "") || body !== String(data.body ?? "");
  return (
    <section aria-label="填写工作项">
      <h3>填写与提交</h3>
      <Status projection={props.projection} />
      {data.feedback && <p>最近反馈：{String(data.feedback)}</p>}
      <label>
        标题
        <input
          aria-label="工作项标题"
          value={title}
          maxLength={200}
          disabled={locked || !allowed(props, "edit")}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, title: event.target.value })
          }
        />
      </label>
      <label>
        说明
        <textarea
          aria-label="工作项说明"
          value={body}
          maxLength={4000}
          disabled={locked || !allowed(props, "edit")}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, body: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !allowed(props, "edit") || !title.trim()}
        onClick={() =>
          void action.run(() => props.execute("edit", { title, body }))
        }
      >
        保存工作项
      </button>
      <button
        type="button"
        disabled={
          locked || !allowed(props, "submit") || changed || !body.trim()
        }
        onClick={() => void action.run(() => props.execute("submit", {}))}
      >
        提交工作项
      </button>
      {changed && <p>这里的输入还是个人草稿，先保存工作项，再提交正式内容。</p>}
      {!allowed(props, "edit") && <p>当前正式状态不允许修改内容。</p>}
      <button
        type="button"
        disabled={locked}
        onClick={() => props.navigate("inspect")}
      >
        查看处理与记录
      </button>
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
function Inspector(props: WorkflowStepProps) {
  const action = useAction();
  if (!props.projection)
    return (
      <section>
        <p>请先打开工作项后查看正式状态。</p>
      </section>
    );
  const { data } = props.projection,
    locked = props.busy || action.pending,
    note = String(props.draft.note ?? "");
  const canReturn = allowed(props, "return"),
    canComplete = allowed(props, "complete");
  const history = Array.isArray(data.history) ? data.history : [];
  return (
    <section aria-label="工作项处理与记录">
      <h3>{String(data.title)}</h3>
      <Status projection={props.projection} />
      <p>{String(data.body ?? "")}</p>
      <p>
        本人模拟处理，用来调试状态机。真实多人审批需要在模块中另外声明职责与授权。
      </p>
      {data.feedback && <p>处理反馈：{String(data.feedback)}</p>}
      <label>
        处理说明
        <textarea
          aria-label="处理说明"
          value={note}
          maxLength={1000}
          disabled={locked || (!canReturn && !canComplete)}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, note: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !canReturn || !note.trim()}
        onClick={() => void action.run(() => props.execute("return", { note }))}
      >
        模拟退回
      </button>
      <button
        type="button"
        disabled={locked || !canComplete || !note.trim()}
        onClick={() =>
          void action.run(() => props.execute("complete", { note }))
        }
      >
        模拟完成
      </button>
      <button
        type="button"
        disabled={locked}
        onClick={() => void action.run(() => props.refresh())}
      >
        重新读取正式状态
      </button>
      {(props.projection.state === "draft" ||
        props.projection.state === "returned") && (
        <button
          type="button"
          disabled={locked}
          onClick={() => props.navigate("edit")}
        >
          继续填写
        </button>
      )}
      <h4>最近办理记录</h4>
      <ol>
        {history.map((entry: any, index: number) => (
          <li key={`${entry.revision}-${index}`}>
            修订 {entry.revision} · {entry.action} ·{" "}
            {entry.from ? (labels[entry.from] ?? entry.from) : "新建"} →{" "}
            {labels[entry.to] ?? entry.to} · {entry.at}
          </li>
        ))}
      </ol>
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
export const stepComponents: WorkflowStepComponents = {
  Picker,
  Editor,
  Inspector,
};
