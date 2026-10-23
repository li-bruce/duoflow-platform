import { useRef, useState } from "react";
import type {
  WorkflowStepComponents,
  WorkflowStepProps,
} from "@platform/workflow-sdk";
import definition from "./flow.json";

const objectType = definition.flows[0].provider.object_type;
const stateLabels: Record<string, string> = Object.fromEntries(
  definition.flows[0].state_machine.states.map((state) => [
    state.id,
    state.title,
  ]),
);
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
        reason instanceof Error ? reason.message : "操作未完成，请重试。",
      );
    } finally {
      running.current = false;
      setPending(false);
    }
  }
  return { error, pending, run };
}
function Picker(props: WorkflowStepProps) {
  const action = useAction(),
    locked = props.busy || action.pending;
  const available = props.actions.some(
    (item) => item.id === "create" && item.available,
  );
  const project_id = String(props.draft.project_id ?? ""),
    title = String(props.draft.title ?? ""),
    body = String(props.draft.body ?? "");
  const records = props.objects.filter(
    (item) => item.object_ref.object_type === objectType,
  );
  const more = props.objectPages.some(
    (item) => item.object_type === objectType && item.next_cursor,
  );
  return (
    <section aria-label="选择或创建记录">
      <h3>当前授权项目的记录</h3>
      <p>
        同项目、同机构授权范围内的参与者可读；编辑、提交和审核另外核验职责。
      </p>
      <ul>
        {records.map((record) => (
          <li key={record.object_ref.object_id}>
            <button
              type="button"
              disabled={locked}
              onClick={() =>
                void action.run(() =>
                  props.openObject(record.object_ref.object_id),
                )
              }
            >
              {String(record.data.title)} ·{" "}
              {stateLabels[record.state] ?? record.state}
            </button>{" "}
            <small>{String(record.data.project_id)}</small>
          </li>
        ))}
      </ul>
      {!records.length && <p>当前没有可读记录。</p>}
      {more && (
        <button
          type="button"
          disabled={locked}
          onClick={() =>
            void action.run(() => props.loadMoreObjects(objectType))
          }
        >
          加载更多记录
        </button>
      )}
      <h3>创建草稿</h3>
      <p>项目编号请使用管理员提供的服务端已登记编号。</p>
      <label>
        项目编号
        <input
          aria-label="项目编号"
          value={project_id}
          disabled={locked || !available}
          onChange={(event) =>
            props.updateDraft({
              ...props.draft,
              project_id: event.target.value,
            })
          }
        />
      </label>
      <label>
        标题
        <input
          aria-label="新记录标题"
          value={title}
          maxLength={200}
          disabled={locked || !available}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, title: event.target.value })
          }
        />
      </label>
      <label>
        内容
        <textarea
          aria-label="新记录内容"
          value={body}
          maxLength={4000}
          disabled={locked || !available}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, body: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !available || !title.trim() || !project_id.trim()}
        onClick={() =>
          void action.run(() =>
            props.execute("create", { project_id, title, body }),
          )
        }
      >
        创建记录
      </button>
      {!available && <p>当前没有创建记录权限。</p>}
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
function Editor(props: WorkflowStepProps) {
  const action = useAction();
  if (!props.projection)
    return (
      <section>
        <h3>填写与提交</h3>
        <p>请先选择或创建一条记录。</p>
      </section>
    );
  const source = props.projection.data,
    locked = props.busy || action.pending;
  const editable = props.actions.some(
    (item) => item.id === "edit" && item.available,
  );
  const submittable = props.actions.some(
    (item) => item.id === "submit" && item.available,
  );
  const title = String(props.draft.title ?? source.title ?? ""),
    body = String(props.draft.body ?? source.body ?? "");
  const changed =
    title !== String(source.title ?? "") || body !== String(source.body ?? "");
  return (
    <section aria-label="填写与提交">
      <h3>填写与提交</h3>
      <p>
        当前修订 {props.projection.revision} ·{" "}
        {stateLabels[props.projection.state] ?? props.projection.state}
      </p>
      <label>
        标题
        <input
          aria-label="记录标题"
          value={title}
          maxLength={200}
          disabled={locked || !editable}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, title: event.target.value })
          }
        />
      </label>
      <label>
        内容
        <textarea
          aria-label="记录内容"
          value={body}
          maxLength={4000}
          disabled={locked || !editable}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, body: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !editable || !title.trim()}
        onClick={() =>
          void action.run(() => props.execute("edit", { title, body }))
        }
      >
        保存记录
      </button>
      <button
        type="button"
        disabled={locked || !submittable || changed}
        onClick={() => void action.run(() => props.execute("submit", {}))}
      >
        提交审核
      </button>
      {changed && editable && <p>提交前请先保存正式记录。</p>}
      {!editable && <p>当前记录只读。</p>}
      <button
        type="button"
        disabled={locked}
        onClick={() => props.navigate("review")}
      >
        查看审核与结果
      </button>
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
function Review(props: WorkflowStepProps) {
  const action = useAction();
  if (!props.projection)
    return (
      <section>
        <h3>审核与结果</h3>
        <p>请先选择一条记录。</p>
      </section>
    );
  const locked = props.busy || action.pending,
    available = props.actions.some(
      (item) => item.id === "approve" && item.available,
    );
  const note = String(props.draft.note ?? "");
  return (
    <section aria-label="审核与结果">
      <h3>审核与结果</h3>
      <p>
        {String(props.projection.data.title)} ·{" "}
        {stateLabels[props.projection.state] ?? props.projection.state}
      </p>
      <p>{String(props.projection.data.body ?? "")}</p>
      {props.projection.data.review_note && (
        <p>审核意见：{String(props.projection.data.review_note)}</p>
      )}
      <label>
        审核意见
        <textarea
          aria-label="审核意见"
          value={note}
          maxLength={1000}
          disabled={locked || !available}
          onChange={(event) =>
            props.updateDraft({ ...props.draft, note: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        disabled={locked || !available || !note.trim()}
        onClick={() =>
          void action.run(() => props.execute("approve", { note }))
        }
      >
        审核通过
      </button>
      {!available && (
        <p>仅具有本项目范围审核权限且不是作者的办理人，可审核待审记录。</p>
      )}
      <button
        type="button"
        disabled={locked}
        onClick={() => void action.run(() => props.refresh())}
      >
        重新读取结果
      </button>
      {action.error && <p role="alert">{action.error}</p>}
    </section>
  );
}
export const stepComponents: WorkflowStepComponents = {
  Picker,
  Editor,
  Review,
};
