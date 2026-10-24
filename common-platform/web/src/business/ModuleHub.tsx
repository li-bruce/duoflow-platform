import { useEffect, useRef, useState } from "react";
import { ArrowRight, Boxes, RefreshCw, Search, X } from "lucide-react";
import { api, post, requestId } from "../api";
import ResultView, { ViewRenderer, type UIContext } from "../ResultView";
import type { Result, View } from "../types";
import { Empty, ErrorNotice, Loading } from "../ui";
import type { BusinessModule, PublicFlow } from "./types";
import "./businessHub.css";

/** The URL holds only a reference. Shared hosts reload permissions and saved state. */
function restoredView(): View | undefined {
  const query = new URLSearchParams(location.hash.split("?")[1]);
  const kind = query.get("kind");
  const instance = query.get("instance");
  if (instance && (kind === "workflow" || kind === "interaction"))
    return { type: kind, data: { instance_id: instance } };
  const capability = query.get("capability");
  if (kind === "form" && capability)
    return { type: "form", data: { capability_id: capability, input: {} } };
}

function rememberView(view: View) {
  const query = new URLSearchParams({ kind: view.type });
  if (view.data.instance_id)
    query.set("instance", String(view.data.instance_id));
  if (view.data.capability_id)
    query.set("capability", String(view.data.capability_id));
  location.hash = `module-hub?${query}`;
}

export default function ModuleHub({ context }: { context: UIContext }) {
  const [modules, setModules] = useState<BusinessModule[]>();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [view, setView] = useState(restoredView);
  const [result, setResult] = useState<Result>();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<unknown>();
  const attempts = useRef<Record<string, string>>({});
  const sequence = useRef(0);

  async function load() {
    const current = ++sequence.current;
    setError(undefined);
    try {
      const response = await api<{ items: BusinessModule[] }>(
        "/business-modules",
      );
      if (sequence.current === current) setModules(response.items);
    } catch (failure) {
      if (sequence.current === current) setError(failure);
    }
  }

  useEffect(() => {
    void load();
    const restore = () => {
      setView(restoredView());
      setResult(undefined);
    };
    window.addEventListener("hashchange", restore);
    return () => {
      sequence.current++;
      window.removeEventListener("hashchange", restore);
    };
  }, [context.user.id]);

  async function open(module: BusinessModule, flow: PublicFlow) {
    const identity = `${module.module_id}/${flow.id}/${flow.version}`;
    attempts.current[identity] ||= requestId();
    setBusy(identity);
    setError(undefined);
    try {
      const response = await post<{ view: View }>(
        `/business-modules/${encodeURIComponent(module.module_id)}/flows/${encodeURIComponent(flow.id)}/open`,
        { version: flow.version, idempotency_key: attempts.current[identity] },
      );
      setView(response.view);
      setResult(undefined);
      rememberView(response.view);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy("");
    }
  }

  const text = query.trim().toLocaleLowerCase();
  const visible = (modules || []).filter(
    (module) =>
      (!selected || selected === module.module_id) &&
      [
        module.title,
        module.description,
        ...module.flows.map((flow) => `${flow.title} ${flow.description}`),
      ].some((value) => value.toLocaleLowerCase().includes(text)),
  );
  const hostContext = { ...context, onResult: setResult };

  return (
    <div className="business-hub">
      <header className="business-hub-header">
        <div>
          <span className="eyebrow">模块业务</span>
          <h2>选择一项业务，开始办理</h2>
          <p>
            每个模块列出当前可用的业务。打开后可以填写、提交，也可以在组件中转到对话继续。
          </p>
        </div>
        <button className="button secondary" onClick={() => void load()}>
          <RefreshCw size={16} />
          刷新目录
        </button>
      </header>
      <ErrorNotice error={error} />
      <div className="business-hub-filter">
        <label>
          <Search size={17} />
          <input
            aria-label="搜索模块或业务"
            placeholder="搜索模块或业务"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="选择模块"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">全部模块</option>
          {modules?.map((module) => (
            <option key={module.module_id} value={module.module_id}>
              {module.title}
            </option>
          ))}
        </select>
      </div>
      {!modules && !error && <Loading label="正在读取业务目录…" />}
      {modules && !visible.length && (
        <Empty
          title={
            modules.length ? "没有找到相应业务" : "当前没有可使用的模块业务"
          }
          description={
            modules.length
              ? "可以换一个名称查找。"
              : "模块完成接入并获得相应授权后，会出现在这里。"
          }
        />
      )}
      <div className="business-module-list">
        {visible.map((module) => (
          <section key={module.module_id} className="business-module">
            <header>
              <Boxes size={22} />
              <div>
                <h3>{module.title}</h3>
                <p>{module.description}</p>
              </div>
              <small>v{module.version}</small>
            </header>
            <div className="business-entry-list">
              {module.flows.map((flow) => (
                <article key={flow.id}>
                  <div>
                    <h4>{flow.title}</h4>
                    <p>{flow.description}</p>
                    <details>
                      <summary>查看业务范围</summary>
                      <dl>
                        <dt>输入</dt>
                        <dd>
                          {Object.keys(flow.input_schema.properties || {}).join(
                            "、",
                          ) || "打开后填写"}
                        </dd>
                        <dt>返回</dt>
                        <dd>
                          {Object.keys(
                            flow.output_schema.properties || {},
                          ).join("、") || "办理结果"}
                        </dd>
                        <dt>业务编号</dt>
                        <dd>
                          <code>{flow.id}</code>
                        </dd>
                      </dl>
                    </details>
                  </div>
                  <button
                    className="button primary"
                    disabled={Boolean(busy)}
                    onClick={() => void open(module, flow)}
                  >
                    {busy === `${module.module_id}/${flow.id}/${flow.version}`
                      ? "正在打开…"
                      : "打开业务"}
                    <ArrowRight size={16} />
                  </button>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
      {view && (
        <section className="business-opened" aria-label="当前业务">
          <header>
            <h3>当前业务</h3>
            <button
              className="button secondary small"
              onClick={() => {
                setView(undefined);
                setResult(undefined);
                location.hash = "module-hub";
              }}
            >
              <X size={15} />
              收起
            </button>
          </header>
          <ViewRenderer view={view} context={hostContext} />
          {result && <ResultView result={result} context={hostContext} />}
        </section>
      )}
    </div>
  );
}
