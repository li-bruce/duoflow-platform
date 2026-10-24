// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { api, post } from "../api";
import CompositionEditor from "./CompositionEditor";
import CompositionRunView from "./CompositionRunView";
import type { BusinessModule } from "../business/types";
import type { UIContext } from "../ResultView";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  api: vi.fn(),
  post: vi.fn(),
  requestId: vi.fn(),
}));
vi.mock("../workflows/FlowHost", () => ({
  default: ({ instanceId }: { instanceId: string }) => (
    <div>页面组件 {instanceId}</div>
  ),
}));
vi.mock("../workflows/ConversationWorkflow", () => ({
  default: ({ instanceId }: { instanceId: string }) => (
    <div>对话组件 {instanceId}</div>
  ),
}));
const string = { type: "string", minLength: 1, maxLength: 100 };
const modules = ["one", "two"].map((id) => ({
  module_id: id,
  title: id,
  flows: [
    {
      id: id + ".flow",
      version: "1.0.0",
      title: id + "业务",
      description: "业务用途",
      input_schema: {
        type: "object",
        properties: { text: { ...string, title: "文字" } },
        required: ["text"],
      },
      output_schema: { type: "object", properties: { text: string } },
      required_permissions: [],
      data_access: [],
    },
  ],
})) as unknown as BusinessModule[];
const context = {
  user: { id: "owner" },
  modules: [],
  capabilities: [],
  onChanged: vi.fn(),
} as unknown as UIContext;
const waiting = {
  id: "run-a",
  title: "组合实例",
  status: "waiting",
  revision: 4,
  content_available: true,
  steps: [
    {
      id: "step-a",
      title: "第一步",
      status: "waiting",
      input: {},
      view: { type: "workflow", data: { instance_id: "same-instance" } },
    },
  ],
};
beforeEach(async () => {
  vi.clearAllMocks();
  const { requestId } = await import("../api");
  let index = 0;
  vi.mocked(requestId).mockImplementation(
    () => `${String(++index).padStart(8, "0")}-0000-0000-0000-000000000000`,
  );
  vi.mocked(api).mockResolvedValue(waiting);
  vi.mocked(post).mockResolvedValue({ valid: true });
});
afterEach(cleanup);

it("builds explicit connections to earlier outputs and submits exact schemas", async () => {
  render(<CompositionEditor modules={modules} onSaved={vi.fn()} />);
  for (const id of ["one", "two"]) {
    fireEvent.change(
      screen.getByRole("combobox", { name: "选择要加入的业务" }),
      { target: { value: `${id}/${id}.flow` } },
    );
    fireEvent.click(screen.getByRole("button", { name: "加入流程" }));
  }
  const selector = screen.getByRole("combobox", { name: "2 文字的数据来源" });
  const option = [...(selector as HTMLSelectElement).options].find((item) =>
    item.text.includes("one业务 → text"),
  )!;
  fireEvent.change(selector, { target: { value: option.value } });
  fireEvent.click(screen.getByRole("button", { name: "检查连接" }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  const body = vi.mocked(post).mock.calls[0][1] as {
    document: {
      input_schema: { properties: object };
      steps: { id: string; bindings: object }[];
    };
  };
  expect(body.document.steps[1].bindings).toEqual({
    text: {
      source: "step",
      step_id: body.document.steps[0].id,
      path: ["text"],
    },
  });
  expect(Object.keys(body.document.input_schema.properties)).toHaveLength(1);
});

it("refreshes a persisted run by reading only and shares the original component", async () => {
  render(<CompositionRunView runId="run-a" context={context} />);
  await screen.findByText("页面组件 same-instance");
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(post).not.toHaveBeenCalled();
});

it("uses the same instance in the conversation", async () => {
  render(
    <CompositionRunView
      runId="run-a"
      context={{ ...context, conversationId: "conversation-a" }}
    />,
  );
  await screen.findByText("对话组件 same-instance");
  expect(post).not.toHaveBeenCalled();
});

it("confirms only the displayed step revision and hides content after revoked access", async () => {
  vi.mocked(api).mockResolvedValue({
    ...waiting,
    status: "confirming",
    steps: [
      {
        id: "step-a",
        title: "第一步",
        status: "confirming",
        input: { text: "待确认内容" },
      },
    ],
  });
  vi.mocked(post).mockResolvedValue({
    ...waiting,
    status: "blocked",
    content_available: false,
    steps: [
      {
        id: "step-a",
        title: "第一步",
        status: "confirming",
        input: {},
        output: null,
      },
    ],
    error: { code: "authorization_changed", message: "授权已变化" },
  });
  render(<CompositionRunView runId="run-a" context={context} />);
  fireEvent.click(await screen.findByRole("button", { name: "确认执行本步" }));
  await screen.findByText("授权已变化");
  expect(post).toHaveBeenCalledWith("/compositions/runs/run-a/advance", {
    expected_revision: 4,
    confirm_step: "step-a",
    reconcile: false,
  });
  expect(screen.queryByText("待确认内容")).toBeNull();
  expect(screen.queryByRole("button", { name: "确认执行本步" })).toBeNull();
});

it("keeps a delayed confirmation reply out of a different run", async () => {
  const confirming = {
    ...waiting,
    status: "confirming",
    steps: [{ id: "step-a", title: "第一步", status: "confirming", input: {} }],
  };
  vi.mocked(api)
    .mockResolvedValueOnce(confirming)
    .mockResolvedValue({
      ...waiting,
      id: "run-b",
      title: "另一项办理",
    });
  let complete!: (value: unknown) => void;
  vi.mocked(post).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const view = render(<CompositionRunView runId="run-a" context={context} />);
  fireEvent.click(await screen.findByRole("button", { name: "确认执行本步" }));
  view.rerender(<CompositionRunView runId="run-b" context={context} />);
  await screen.findByRole("heading", { name: "另一项办理" });
  await act(async () =>
    complete({ ...confirming, status: "succeeded", title: "旧办理的延迟结果" }),
  );
  await waitFor(() =>
    expect(screen.queryByText("旧办理的延迟结果")).toBeNull(),
  );
  expect(screen.getByRole("heading", { name: "另一项办理" })).toBeTruthy();
});
