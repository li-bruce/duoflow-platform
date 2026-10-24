import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { ErrorNotice, Loading } from "../ui";

type Node = {
  id: string;
  title: string;
  kind: string;
  step_id: string;
  description?: string;
  current_state?: boolean;
  business_revision?: number;
  activity?: string;
  atom_ids?: string[];
};
type Graph = {
  content_available: boolean;
  groups: { id: string; title: string; module_id: string }[];
  nodes: Node[];
  edges: {
    id: string;
    from: string;
    to: string;
    kind: string;
    title: string;
  }[];
  observations: {
    step_id: string;
    freshness: string;
    gaps?: number[];
    dropped?: number;
    message?: string;
  }[];
};
const kindTitle: Record<string, string> = {
  entry: "业务入口",
  business: "业务处理",
  state: "正式状态",
  data: "数据",
  decision: "条件判断",
};
const freshnessTitle: Record<string, string> = {
  empty: "等待调用记录",
  unavailable: "观察暂不可用",
  active: "正在接收",
  complete: "本次调用记录已结束",
  stale: "较长时间没有更新",
  receipt: "已读取本地调用记录",
};
const activityTitle: Record<string, string> = {
  started: "正在调用",
  running: "正在执行",
  succeeded: "调用成功",
  failed: "调用失败",
  waiting: "等待办理",
  waiting_run: "等待结果",
  confirming: "等待确认",
  pending: "尚未开始",
};

/** All connections come from the server graph; color never advances business state. */
export default function CompositionGraph({ runId }: { runId: string }) {
  const [graph, setGraph] = useState<Graph>();
  const [error, setError] = useState<unknown>();
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [updated, setUpdated] = useState("");
  const root = useRef<SVGSVGElement>(null);
  useEffect(() => {
    let active = true,
      pending = false;
    const controller = new AbortController();
    async function load() {
      if (pending) return;
      pending = true;
      try {
        const value = await api<Graph>(
          `/compositions/runs/${encodeURIComponent(runId)}/graph`,
          { signal: controller.signal },
        );
        if (active) {
          setGraph(value);
          setError(undefined);
          setUpdated(new Date().toLocaleTimeString("zh-CN"));
        }
      } catch (failure) {
        if (active) {
          setError(failure);
          setGraph(undefined);
        }
      } finally {
        pending = false;
      }
    }
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [runId]);
  const layout = useMemo(() => {
    const points = new Map<string, { x: number; y: number }>();
    let height = 260;
    graph?.groups.forEach((group, index) => {
      graph.nodes
        .filter((node) => node.step_id === group.id)
        .forEach((node, row) => {
          const point = { x: index * 310 + 24, y: 66 + row * 74 };
          points.set(node.id, point);
          height = Math.max(height, point.y + 95);
        });
    });
    return {
      points,
      width: Math.max(350, (graph?.groups.length || 1) * 310),
      height,
    };
  }, [graph]);
  const chosen = graph?.nodes.find((node) => node.id === selected);
  const related =
    graph?.edges.filter(
      (edge) => edge.from === selected || edge.to === selected,
    ) || [];
  const matches = search.trim()
    ? graph?.nodes.filter((node) =>
        `${node.title} ${node.id}`
          .toLocaleLowerCase()
          .includes(search.trim().toLocaleLowerCase()),
      ) || []
    : [];
  function select(id: string) {
    setSelected(id);
    root.current
      ?.querySelector(`[data-node-id="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  return (
    <section className="composition-graph" aria-label="整体业务图">
      <header>
        <div>
          <h3>整体业务图</h3>
          <p>
            点击节点查看前后关系。绿色表示当前正式状态，蓝色表示本次调用成功。
          </p>
        </div>
        {updated && <small>最近读取 {updated}</small>}
      </header>
      <ErrorNotice error={error} />
      <label className="field">
        查找业务、状态或数据节点
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="输入名称或编号"
        />
      </label>
      {search.trim() && (
        <div className="composition-graph-matches">
          {matches.map((node) => (
            <button
              key={node.id}
              className="button secondary small"
              onClick={() => select(node.id)}
            >
              {node.title}
            </button>
          ))}
          {!matches.length && <p>没有找到相应节点。</p>}
        </div>
      )}
      {!graph && !error && <Loading label="正在读取业务图…" />}
      {graph && (
        <>
          <div
            className="composition-graph-canvas"
            tabIndex={0}
            aria-label="业务图，可滚动查看"
          >
            <svg
              ref={root}
              width={layout.width}
              height={layout.height}
              role="img"
              aria-label="组合中的业务、状态及数据连接"
            >
              <defs>
                <marker
                  id={`arrow-${runId}`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#7c8c99" />
                </marker>
              </defs>
              {graph.groups.map((group, index) => (
                <text
                  x={index * 310 + 24}
                  y={30}
                  key={group.id}
                  className="composition-graph-group"
                >
                  {group.title.length > 18
                    ? group.title.slice(0, 18) + "…"
                    : group.title}
                </text>
              ))}
              {graph.edges
                .filter((edge) => edge.kind !== "contains")
                .map((edge) => {
                  const a = layout.points.get(edge.from),
                    b = layout.points.get(edge.to);
                  if (!a || !b) return null;
                  const same = a.x === b.x;
                  const path = same
                    ? `M ${a.x + 250} ${a.y + 25} C ${a.x + 290} ${a.y + 25}, ${b.x + 290} ${b.y + 25}, ${b.x + 250} ${b.y + 25}`
                    : `M ${a.x + 250} ${a.y + 25} C ${a.x + 280} ${a.y + 25}, ${b.x - 20} ${b.y + 25}, ${b.x} ${b.y + 25}`;
                  return (
                    <path
                      key={edge.id}
                      d={path}
                      fill="none"
                      stroke={related.includes(edge) ? "#176556" : "#acb9c2"}
                      strokeWidth={related.includes(edge) ? 2.5 : 1.3}
                      markerEnd={`url(#arrow-${runId})`}
                    >
                      <title>{edge.title}</title>
                    </path>
                  );
                })}
              {graph.nodes.map((node) => {
                const point = layout.points.get(node.id)!;
                return (
                  <g
                    key={node.id}
                    data-node-id={node.id}
                    transform={`translate(${point.x},${point.y})`}
                    tabIndex={0}
                    role="button"
                    aria-label={`${kindTitle[node.kind] || node.kind}：${node.title}${node.current_state ? "，当前状态" : ""}`}
                    aria-pressed={selected === node.id}
                    onClick={() => setSelected(node.id)}
                    onKeyDown={(event) => {
                      if (["Enter", " "].includes(event.key)) {
                        event.preventDefault();
                        setSelected(node.id);
                      }
                    }}
                  >
                    <rect
                      width={250}
                      height={54}
                      rx={8}
                      fill={
                        node.current_state
                          ? "#dff0e4"
                          : node.activity === "succeeded"
                            ? "#e5eff9"
                            : "#fff"
                      }
                      stroke={selected === node.id ? "#176556" : "#c2cfd7"}
                      strokeWidth={selected === node.id ? 2.5 : 1}
                    />
                    <text x={12} y={21}>
                      {node.title.length > 20
                        ? node.title.slice(0, 20) + "…"
                        : node.title}
                    </text>
                    <text x={12} y={41} className="composition-graph-caption">
                      {kindTitle[node.kind] || node.kind}
                      {node.current_state
                        ? " · 当前状态"
                        : node.activity
                          ? " · " +
                            (activityTitle[node.activity] || node.activity)
                          : ""}
                    </text>
                    <title>{node.title}</title>
                  </g>
                );
              })}
            </svg>
          </div>
          {chosen && (
            <section className="composition-node-details">
              <h4>{chosen.title}</h4>
              <p>{chosen.description}</p>
              {chosen.current_state && (
                <p>当前正式状态 · 对象版本 {chosen.business_revision}</p>
              )}
              <code>{chosen.id}</code>
              <ul>
                {related.map((edge) => (
                  <li key={edge.id}>
                    {graph.nodes.find((node) => node.id === edge.from)?.title} →{" "}
                    {graph.nodes.find((node) => node.id === edge.to)?.title}：
                    {edge.title}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="composition-observation-status">
            {graph.observations.map((item) => (
              <p key={item.step_id}>
                {graph.groups.find((group) => group.id === item.step_id)?.title}
                ：{freshnessTitle[item.freshness] || item.freshness}
                {Boolean(item.gaps?.length) &&
                  `，缺少 ${item.gaps!.length} 条记录`}
                {Boolean(item.dropped) &&
                  `，模块报告 ${item.dropped} 条记录未送达`}
              </p>
            ))}
          </div>
          {!graph.content_available && (
            <p>当前授权已变化，图中仅保留本人的办理进度。</p>
          )}
        </>
      )}
    </section>
  );
}
