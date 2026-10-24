// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import ModuleHub from "./ModuleHub";
import { api, post } from "../api";
import type { UIContext } from "../ResultView";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  api: vi.fn(),
  post: vi.fn(),
  requestId: () => "open-key",
}));
vi.mock("../ResultView", () => ({
  default: () => null,
  ViewRenderer: ({
    view,
  }: {
    view: { type: string; data: Record<string, unknown> };
  }) => (
    <div>
      组件 {view.type} / {String(view.data.instance_id)}
    </div>
  ),
}));
const context = {
  user: { id: "owner" },
  modules: [],
  capabilities: [],
  onChanged: vi.fn(),
} as unknown as UIContext;
const entry = {
  id: "sample.start",
  version: "1.2.0",
  title: "办理申请",
  description: "填写申请并查看办理进度",
  input_schema: {},
  output_schema: {},
};
beforeEach(() => {
  vi.clearAllMocks();
  location.hash = "#module-hub";
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        module_id: "sample",
        title: "申请模块",
        description: "统一办理入口",
        version: "1.0.0",
        flows: [entry],
      },
    ],
  });
  vi.mocked(post).mockResolvedValue({
    view: { type: "workflow", data: { instance_id: "saved-instance" } },
  });
});
afterEach(cleanup);

it("opens the selected version through the server and remembers only the host reference", async () => {
  render(<ModuleHub context={context} />);
  fireEvent.click(await screen.findByRole("button", { name: "打开业务" }));
  await screen.findByText("组件 workflow / saved-instance");
  expect(post).toHaveBeenCalledWith(
    "/business-modules/sample/flows/sample.start/open",
    { version: "1.2.0", idempotency_key: "open-key" },
  );
  expect(location.hash).toContain("instance=saved-instance");
});

it("restores an existing host after refresh without creating or executing anything", async () => {
  location.hash = "#module-hub?kind=workflow&instance=existing";
  render(<ModuleHub context={context} />);
  await screen.findByRole("heading", { name: "申请模块" });
  expect(screen.getByText("组件 workflow / existing")).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});

it("searches by business name and reports an empty authorized catalog", async () => {
  render(<ModuleHub context={context} />);
  await screen.findByRole("heading", { name: "申请模块" });
  fireEvent.change(screen.getByRole("textbox", { name: "搜索模块或业务" }), {
    target: { value: "未知业务" },
  });
  expect(screen.getByText("没有找到相应业务")).toBeTruthy();
  vi.mocked(api).mockResolvedValue({ items: [] });
  fireEvent.click(screen.getByRole("button", { name: "刷新目录" }));
  await screen.findByText("当前没有可使用的模块业务");
});

it("keeps the same open key after a connection failure", async () => {
  vi.mocked(post).mockRejectedValueOnce(new Error("连接中断"));
  render(<ModuleHub context={context} />);
  fireEvent.click(await screen.findByRole("button", { name: "打开业务" }));
  await screen.findByText("连接中断");
  fireEvent.click(screen.getByRole("button", { name: "打开业务" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(vi.mocked(post).mock.calls[0]).toEqual(vi.mocked(post).mock.calls[1]);
});
