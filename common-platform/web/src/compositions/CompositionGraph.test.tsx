// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import CompositionGraph from "./CompositionGraph";
import { api } from "../api";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  api: vi.fn(),
}));
const graph = {
  content_available: true,
  groups: [
    { id: "a", title: "提交业务", module_id: "module.a" },
    { id: "b", title: "后续业务", module_id: "module.b" },
  ],
  nodes: [
    {
      id: "a::state",
      title: "已提交",
      kind: "state",
      step_id: "a",
      current_state: true,
      business_revision: 3,
    },
    { id: "b::data", title: "来源编号", kind: "data", step_id: "b" },
  ],
  edges: [
    {
      id: "edge",
      from: "a::state",
      to: "b::data",
      kind: "data",
      title: "传递编号",
    },
  ],
  observations: [{ step_id: "a", freshness: "stale", gaps: [2], dropped: 0 }],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockResolvedValue(graph);
});
afterEach(cleanup);

it("uses real state and connection labels, with keyboard access to details", async () => {
  render(<CompositionGraph runId="run" />);
  const node = await screen.findByRole("button", {
    name: "正式状态：已提交，当前状态",
  });
  fireEvent.keyDown(node, { key: "Enter" });
  expect(screen.getByText("当前正式状态 · 对象版本 3")).toBeTruthy();
  expect(screen.getByText("已提交 → 来源编号：传递编号")).toBeTruthy();
  expect(
    screen.getByText("提交业务：较长时间没有更新，缺少 1 条记录"),
  ).toBeTruthy();
});

it("searches actual graph nodes without inventing a match", async () => {
  render(<CompositionGraph runId="run" />);
  await screen.findByRole("button", { name: "数据：来源编号" });
  fireEvent.change(
    screen.getByRole("textbox", { name: "查找业务、状态或数据节点" }),
    { target: { value: "不存在" } },
  );
  expect(screen.getByText("没有找到相应节点。")).toBeTruthy();
});

it("separates completed composition steps from successful calls without inventing traces", async () => {
  vi.mocked(api).mockResolvedValue({
    ...graph,
    nodes: [
      { id: "a::entry", title: "已完成的人工业务", kind: "entry", step_id: "a", activity: "succeeded" },
      { id: "b::atom", title: "实际调用", kind: "business", step_id: "b", activity: "succeeded" },
    ],
    edges: [],
    observations: [{ step_id: "a", freshness: "empty" }],
  });
  render(<CompositionGraph runId="completed-workflows" />);
  expect(await screen.findByText("业务入口 · 步骤已完成")).toBeTruthy();
  expect(screen.getByText("业务处理 · 调用成功")).toBeTruthy();
  expect(screen.queryByText("业务入口 · 调用成功")).toBeNull();
  expect(screen.getByText("提交业务：等待调用记录")).toBeTruthy();
});
