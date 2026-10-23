import type { InteractionProps } from "@platform/interaction-sdk";

/** Shared page/conversation view; the host owns authentication and persistence. */
export default function TextTool({
  state,
  result,
  busy,
  actions,
  updateState,
  invokeAction,
}: InteractionProps) {
  const text = String(state.text ?? "");
  const canRun = actions.some(
    (action) => action.id === "analyze" && action.available,
  );
  const output = result?.data;
  return (
    <section aria-label="文本整理工具">
      <label className="field">
        待整理文本
        <textarea
          aria-label="待整理文本"
          rows={7}
          maxLength={20000}
          value={text}
          disabled={busy}
          onChange={(event) =>
            updateState({ ...state, text: event.target.value })
          }
        />
      </label>
      <p className="subtle">
        最多 20000 个字符。统一换行、整理空白，并保留段落。
      </p>
      <button
        type="button"
        className="button primary"
        disabled={busy || !canRun || !text.trim()}
        onClick={() => void invokeAction("analyze")}
      >
        {busy ? "正在处理…" : "整理与统计"}
      </button>
      {!canRun && <p role="status">当前没有工具使用权限，或模块暂不可用。</p>}
      {output && (
        <div aria-label="文本整理结果" aria-live="polite">
          <p>
            {Number(output.character_count)} 个字符 ·{" "}
            {Number(output.line_count)} 行 · {Number(output.segment_count)}{" "}
            个空白分隔片段
          </p>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {String(output.normalized_text ?? "")}
          </pre>
        </div>
      )}
    </section>
  );
}
