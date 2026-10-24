import { lazy, Suspense, useEffect, useState } from "react";
import { api, post } from "../api";
import { isPlatformAdmin } from "../types";
import { ErrorNotice, Loading } from "../ui";
import type { UIContext } from "../ResultView";
import type { BusinessModule } from "../business/types";
import CompositionEditor from "./CompositionEditor";
import CompositionStart from "./CompositionStart";
import { CompositionRoleAssignment } from "./CompositionRoles";
import { statusLabel, type SavedComposition } from "./types";
import "./compositions.css";

const CompositionRunView = lazy(() => import("./CompositionRunView"));

type RunSummary = { id: string; title: string; status: string };
const selectedRun = () =>
  new URLSearchParams(location.hash.split("?")[1]).get("run") || "";

export default function CompositionWorkbench({
  context,
}: {
  context: UIContext;
}) {
  const [modules, setModules] = useState<BusinessModule[]>();
  const [definitions, setDefinitions] = useState<SavedComposition[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [tab, setTab] = useState<"editor" | "saved" | "runs">(
    selectedRun() ? "runs" : "editor",
  );
  const [selected, setSelected] = useState<SavedComposition>();
  const [run, setRun] = useState(selectedRun);
  const [error, setError] = useState<unknown>();
  async function load() {
    try {
      const [catalog, saved, existing] = await Promise.all([
        api<{ items: BusinessModule[] }>("/business-modules"),
        api<{ items: SavedComposition[] }>("/compositions/definitions"),
        api<{ items: RunSummary[] }>("/compositions/runs"),
      ]);
      setModules(catalog.items);
      setDefinitions(saved.items);
      setRuns(existing.items);
    } catch (failure) {
      setError(failure);
    }
  }
  useEffect(() => {
    void load();
    const restore = () => {
      setRun(selectedRun());
      if (selectedRun()) setTab("runs");
    };
    window.addEventListener("hashchange", restore);
    return () => window.removeEventListener("hashchange", restore);
  }, [context.user.id]);
  function openRun(id: string) {
    setRun(id);
    setTab("runs");
    location.hash = `compositions?run=${encodeURIComponent(id)}`;
  }
  async function publish() {
    if (!selected) return;
    setError(undefined);
    try {
      const value = await post<{ shared: boolean }>(
        `/compositions/definitions/${selected.id}/publication`,
        { shared: !selected.shared },
      );
      setSelected({ ...selected, shared: value.shared });
      await load();
    } catch (failure) {
      setError(failure);
    }
  }
  return (
    <div className="composition-workbench">
      <nav className="composition-tabs" aria-label="组合工作台">
        {(
          [
            ["editor", "连接业务"],
            ["saved", "已保存组合"],
            ["runs", "办理记录"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            className={tab === value ? "active" : ""}
            aria-pressed={tab === value}
            onClick={() => {
              setTab(value);
              void load();
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <ErrorNotice error={error} />
      {!modules ? (
        <Loading />
      ) : tab === "editor" ? (
        <CompositionEditor
          modules={modules}
          onSaved={(value) => {
            setSelected(value);
            setTab("saved");
            void load();
          }}
        />
      ) : tab === "saved" ? (
        <>
          <div className="composition-saved-list">
            {definitions.map((definition) => (
              <button
                key={definition.id}
                onClick={() => setSelected(definition)}
                aria-pressed={selected?.id === definition.id}
              >
                <strong>{definition.document.title}</strong>
                <span>
                  v{definition.document.version} ·{" "}
                  {definition.document.steps.length} 步
                </span>
              </button>
            ))}
            {!definitions.length && <p>保存后的组合会出现在这里。</p>}
          </div>
          {selected && (
            <>
              <p>
                {selected.shared
                  ? "已发布：具有相应模块权限的用户可以使用此组合。"
                  : "此组合目前由创建者使用。"}
              </p>
              {selected.owned && isPlatformAdmin(context.user) && (
                <button
                  className="button secondary"
                  onClick={() => void publish()}
                >
                  {selected.shared ? "收回公开入口" : "发布给有业务权限的用户"}
                </button>
              )}
              <CompositionStart
                key={selected.id}
                definition={selected}
                onStarted={(value) => {
                  openRun(value.id);
                  void load();
                }}
              />
              <CompositionRoleAssignment
                key={selected.id + "-roles"}
                definition={selected}
                user={context.user}
              />
            </>
          )}
        </>
      ) : (
        <>
          <label className="field">
            选择办理记录
            <select
              value={run}
              onChange={(event) => openRun(event.target.value)}
            >
              <option value="">选择一条记录</option>
              {runs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} · {statusLabel[item.status]}
                </option>
              ))}
            </select>
          </label>
          {run && (
            <Suspense fallback={<Loading label="正在恢复办理记录…" />}>
              <CompositionRunView key={run} runId={run} context={context} />
            </Suspense>
          )}
        </>
      )}
    </div>
  );
}
