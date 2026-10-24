import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ArrowRight, MessageSquare, RefreshCw } from "lucide-react";
import { api, post } from "../api";
import type { UIContext } from "../ResultView";
import FlowHost from "../workflows/FlowHost";
import { ErrorNotice, Loading, PrettyData } from "../ui";
import { statusLabel, type CompositionRun } from "./types";
import "./compositions.css";
import CompositionGraph from "./CompositionGraph";

const ConversationWorkflow = lazy(
  () => import("../workflows/ConversationWorkflow"),
);

type RunProps = { runId: string; context: UIContext };

/** Each run owns its requests and view state, including delayed action replies. */
export default function CompositionRunView(props: RunProps) {
  return <RunContents key={props.runId} {...props} />;
}

function RunContents({ runId, context }: RunProps) {
  const [run, setRun] = useState<CompositionRun>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const loading = useRef(false);
  const acting = useRef(false);
  const load = useCallback(async () => {
    if (loading.current || acting.current) return;
    loading.current = true;
    const current = ++generation.current;
    try {
      const value = await api<CompositionRun>(
        `/compositions/runs/${encodeURIComponent(runId)}`,
      );
      if (mounted.current && generation.current === current) {
        setRun(value);
        setError(undefined);
      }
    } catch (failure) {
      if (mounted.current && generation.current === current) setError(failure);
    } finally {
      loading.current = false;
    }
  }, [runId]);
  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      mounted.current = false;
      generation.current++;
      clearInterval(timer);
    };
  }, [load]);
  async function advance(confirm?: string, reconcile = false) {
    if (!run || acting.current) return;
    acting.current = true;
    setBusy(true);
    setError(undefined);
    const current = ++generation.current;
    try {
      const value = await post<CompositionRun>(
        `/compositions/runs/${runId}/advance`,
        {
          expected_revision: run.revision,
          confirm_step: confirm || null,
          reconcile,
        },
      );
      if (mounted.current && generation.current === current) setRun(value);
    } catch (failure) {
      if (mounted.current && generation.current === current) setError(failure);
    } finally {
      acting.current = false;
      if (mounted.current && generation.current === current) setBusy(false);
    }
  }
  async function attach() {
    if (acting.current) return;
    acting.current = true;
    const current = ++generation.current;
    setBusy(true);
    setError(undefined);
    try {
      const next = await post<{ conversation_id: string }>(
        `/compositions/runs/${runId}/attach`,
      );
      if (!mounted.current || generation.current !== current) return;
      const state = await api<{
        revision: number;
        drafts: Record<string, unknown>;
      }>("/conversations/state");
      if (!mounted.current || generation.current !== current) return;
      await api("/conversations/state", {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: state.revision,
          current_conversation_id: next.conversation_id,
          drafts: state.drafts,
        }),
      });
      if (mounted.current && generation.current === current)
        location.hash = "chat";
    } catch (failure) {
      if (mounted.current && generation.current === current) setError(failure);
    } finally {
      acting.current = false;
      if (mounted.current && generation.current === current) setBusy(false);
    }
  }
  const current = run?.steps.find((step) => step.status !== "succeeded");
  return (
    <section className="composition-run" aria-label="组合办理">
      <ErrorNotice error={error} />
      {!run ? (
        <Loading label="正在恢复办理记录…" />
      ) : (
        <>
          <header>
            <div>
              <span className="eyebrow">组合办理</span>
              <h2>{run.title}</h2>
              <p role="status">{statusLabel[run.status] || run.status}</p>
            </div>
            <div className="composition-actions">
              <button className="button secondary" onClick={() => void load()}>
                <RefreshCw size={15} />
                刷新
              </button>
              {!context.conversationId && (
                <button
                  className="button secondary"
                  disabled={busy || !run.content_available}
                  onClick={() => void attach()}
                >
                  <MessageSquare size={15} />
                  在对话中继续
                </button>
              )}
            </div>
          </header>
          <ol className="composition-sequence" aria-label="业务顺序">
            {run.steps.map((step, index) => (
              <li key={step.id} data-status={step.status}>
                <div>
                  <span>{index + 1}</span>
                  <strong>{step.title}</strong>
                  <small>{statusLabel[step.status]}</small>
                </div>
                {index < run.steps.length - 1 && (
                  <ArrowRight size={20} aria-hidden="true" />
                )}
              </li>
            ))}
          </ol>
          <ErrorNotice error={run.error?.message} />
          <details
            className="composition-live-graph"
            onToggle={(event) => setGraphOpen(event.currentTarget.open)}
          >
            <summary>展开整体业务图</summary>
            {graphOpen && <CompositionGraph runId={runId} />}
          </details>
          {run.content_available && current && (
            <section className="composition-current">
              <h3>{current.title}</h3>
              {current.status === "confirming" && (
                <>
                  <p>请确认本步将使用的内容。</p>
                  <PrettyData data={current.input || {}} />
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => void advance(current.id)}
                  >
                    确认执行本步
                  </button>
                </>
              )}
              {["unknown", "dispatching"].includes(current.status) && (
                <>
                  <p>可检查平台是否已经收到这次调用的结果。</p>
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => void advance(undefined, true)}
                  >
                    核对调用结果
                  </button>
                </>
              )}
              {current.view?.type === "workflow" &&
                (context.conversationId ? (
                  <Suspense fallback={<Loading label="正在恢复对话组件…" />}>
                    <ConversationWorkflow
                      instanceId={String(current.view.data.instance_id)}
                      onChanged={() => void load()}
                    />
                  </Suspense>
                ) : (
                  <FlowHost
                    instanceId={String(current.view.data.instance_id)}
                    onChanged={() => void load()}
                  />
                ))}
            </section>
          )}
          <details className="composition-records">
            <summary>查看各步输入与结果</summary>
            {run.steps.map((step) => (
              <section key={step.id}>
                <h4>
                  {step.title} · {statusLabel[step.status]}
                </h4>
                <ErrorNotice error={step.error?.message} />
                {step.input && (
                  <details>
                    <summary>本步输入</summary>
                    <PrettyData data={step.input} />
                  </details>
                )}
                {step.output && (
                  <details>
                    <summary>本步结果</summary>
                    <PrettyData data={step.output} />
                  </details>
                )}
              </section>
            ))}
          </details>
        </>
      )}
    </section>
  );
}
