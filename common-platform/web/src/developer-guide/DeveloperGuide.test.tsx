// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DeveloperGuide from "./DeveloperGuide";
import SiteRoot from "../SiteRoot";
import CompositionAnalysis from "./CompositionAnalysis";
import {
  EXECUTION_STEPS,
  executionView,
  MAIN_PARTS,
  MODULE_PARTS,
} from "./reference";
vi.mock("../App", () => ({ default: () => <div>实际工作台</div> }));
beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, "", "/#developer-guide");
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    callback(0);
    return 1;
  });
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function chapter(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
}
function advance(count: number) {
  for (let index = 1; index <= count; index++)
    fireEvent.click(
      screen.getByRole("button", {
        name: `下一步：${EXECUTION_STEPS[index].title}`,
      }),
    );
}

describe("framework structure explanation", () => {
  it("opens without business requests and preserves user and workspace routes", async () => {
    const fetch = vi.spyOn(window, "fetch");
    render(<SiteRoot />);
    expect(
      await screen.findByRole("heading", {
        name: "先看各部分怎样组成一个整体",
      }),
    ).toBeTruthy();
    expect(document.title).toBe("框架全貌 · 构造与交互规则");
    expect(fetch).not.toHaveBeenCalled();
    for (const hash of ["#guide", "#developer-guide", "#devtools"]) {
      await act(async () => {
        history.replaceState(null, "", "/" + hash);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      });
      if (hash === "#guide")
        expect(
          await screen.findByRole("navigation", { name: "学习步骤" }),
        ).toBeTruthy();
      if (hash === "#developer-guide")
        expect(
          await screen.findByText("先看各部分怎样组成一个整体"),
        ).toBeTruthy();
      if (hash === "#devtools")
        expect(await screen.findByText("实际工作台")).toBeTruthy();
    }
  });
  it("explains the shared conventions separately from a running service", () => {
    render(<DeveloperGuide />);
    fireEvent.click(screen.getByRole("button", { name: /模块框架.*功能说明/ }));
    const detail = within(screen.getByRole("region", { name: "整体部分说明" }));
    expect(detail.getByRole("heading", { name: "模块框架" })).toBeTruthy();
    expect(detail.getByText(/格式定义、连接代码/)).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: /平台记录.*正式业务记录/ }),
    );
    expect(detail.getByText(/历史对话也不是当前业务状态/)).toBeTruthy();
    expect(screen.queryByText("编写模块")).toBeNull();
  });
  it("lets readers inspect every main and module responsibility, including input output and storage", () => {
    render(<DeveloperGuide />);
    for (const [label, parts, nav, region] of [
      ["2 主框架", MAIN_PARTS, "主框架组成", "主框架部分说明"],
      ["3 模块框架", MODULE_PARTS, "模块框架组成", "模块框架部分说明"],
    ] as const) {
      chapter(label);
      for (const part of parts) {
        fireEvent.click(
          within(screen.getByRole("navigation", { name: nav })).getByRole(
            "button",
            { name: new RegExp(part.title) },
          ),
        );
        const detail = within(screen.getByRole("region", { name: region }));
        expect(detail.getByText(part.receives)).toBeTruthy();
        expect(detail.getByText(part.returns)).toBeTruthy();
        expect(detail.getByText(part.keeps)).toBeTruthy();
      }
    }
    fireEvent.click(screen.getByRole("button", { name: "一次处理的工具" }));
    expect(screen.getByText(/不一定需要正式记录与多步骤流程/)).toBeTruthy();
  });
  it("keeps business state, personal step and execution result separate on success", () => {
    render(<DeveloperGuide />);
    chapter("4 一次交互");
    advance(7);
    const values = within(
      screen.getByRole("group", { name: "这次演示的三种状态" }),
    );
    expect(values.getByText("已提交")).toBeTruthy();
    expect(values.getByText("处理记录")).toBeTruthy();
    expect(values.getByText("处理成功")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从对话操作" }));
    expect(values.getByText("已提交")).toBeTruthy();
  });
  it.each([
    ["permission", 1, "已拒绝", "草稿"],
    ["version", 4, "未保存", "草稿"],
    ["repeat", 3, "返回原回执", "已提交"],
    ["timeout", 5, "结果未确认", "需要重新读取"],
  ] as const)(
    "shows %s without inventing a successful write",
    (scenario, stop, result, business) => {
      render(<DeveloperGuide />);
      chapter("4 一次交互");
      fireEvent.change(screen.getByLabelText("演示情况"), {
        target: { value: scenario },
      });
      advance(stop);
      const values = within(
        screen.getByRole("group", { name: "这次演示的三种状态" }),
      );
      expect(values.getByText(result)).toBeTruthy();
      expect(values.getByText(business)).toBeTruthy();
      expect(
        screen
          .getByRole("button", { name: "这次过程已结束" })
          .hasAttribute("disabled"),
      ).toBe(true);
      if (scenario === "timeout")
        expect(
          screen
            .getByRole("button", { name: "独立服务" })
            .getAttribute("aria-pressed"),
        ).toBe("true");
    },
  );
  it("changes navigation without changing the business state and follows real declared transitions", () => {
    render(<DeveloperGuide />);
    chapter("5 交互规则");
    fireEvent.click(screen.getByRole("button", { name: "处理记录" }));
    expect(
      document.querySelector(".ar-state-graph [aria-current=true]")
        ?.textContent,
    ).toBe("草稿");
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(
      document.querySelector(".ar-state-graph [aria-current=true]")
        ?.textContent,
    ).toBe("已提交");
    fireEvent.click(screen.getByRole("button", { name: "模拟退回" }));
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟完成" }));
    expect(
      document.querySelector(".ar-state-graph [aria-current=true]")
        ?.textContent,
    ).toBe("已完成");
    expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
  });
  it("separates enabling, service health and grants and names the actual scope", () => {
    render(<DeveloperGuide />);
    chapter("6 运行与恢复");
    fireEvent.click(screen.getByRole("button", { name: /处理与观察/ }));
    expect(screen.getByText(/连续 3 次失败/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "当前没有" }));
    expect(screen.getByText(/没有自动跨服务事务与回滚/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "打开调试台" }).getAttribute("href"),
    ).toBe("#devtools");
  });
  it("saves only the new reading position and falls back safely from invalid storage", () => {
    localStorage.setItem("duo-framework-guide-v1", "keep");
    localStorage.setItem("duo-developer-guide-v1", "old-tutorial");
    let view = render(<DeveloperGuide />);
    chapter("3 模块框架");
    view.unmount();
    view = render(<DeveloperGuide />);
    expect(
      screen.getByRole("heading", { name: "模块框架怎样把业务接到整体中？" }),
    ).toBeTruthy();
    expect(localStorage.getItem("duo-framework-guide-v1")).toBe("keep");
    expect(localStorage.getItem("duo-developer-guide-v1")).toBe("old-tutorial");
    view.unmount();
    localStorage.setItem("duo-framework-structure-v1", "{broken");
    render(<DeveloperGuide />);
    expect(
      screen.getByRole("heading", { name: "先看各部分怎样组成一个整体" }),
    ).toBeTruthy();
  });
  it("does not infer a timeout outcome and keeps old-version content distinct", () => {
    expect(executionView(5, "timeout")).toMatchObject({
      business: "需要重新读取",
      result: "结果未确认",
      revision: "尚未确认",
    });
    expect(executionView(4, "version")).toMatchObject({
      business: "草稿",
      result: "未保存",
      revision: "第 2 版",
    });
  });
  it("opens the core analysis directly from the overall structure", () => {
    render(<DeveloperGuide />);
    fireEvent.click(
      screen.getByRole("button", { name: /重点看：模块怎样公开/ }),
    );
    expect(
      screen.getByRole("heading", { name: "怎样把多个模块组成一项业务？" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "当前支持说明" }).textContent,
    ).toContain("每个组合最多 20 步");
  });
  it("describes implemented composition and its explicit boundaries", () => {
    render(<CompositionAnalysis />);
    fireEvent.click(screen.getByRole("button", { name: /把结果交给下一模块/ }));
    expect(
      screen.getByRole("region", { name: "当前支持说明" }).textContent,
    ).toContain("前一步完成后自动继续");
    fireEvent.click(screen.getByRole("button", { name: "主框架怎样组织" }));
    fireEvent.click(screen.getByRole("button", { name: /组合执行与记录/ }));
    expect(screen.getByText(/每次办理产生一个组合编号/)).toBeTruthy();
  });
  it("keeps the same illustrative chain when switching page and conversation", () => {
    render(<CompositionAnalysis />);
    fireEvent.click(screen.getByRole("button", { name: "串联示例" }));
    fireEvent.click(screen.getByRole("button", { name: "执行资料登记" }));
    fireEvent.click(screen.getByRole("button", { name: "对话方式" }));
    expect(document.querySelector(".ca-run")?.textContent).toContain(
      "COM-001 · 已完成 1 / 3 步",
    );
    fireEvent.click(screen.getByRole("button", { name: "执行内容整理" }));
    fireEvent.click(screen.getByRole("button", { name: "页面方式" }));
    expect(document.querySelector(".ca-run")?.textContent).toContain(
      "COM-001 · 已完成 2 / 3 步",
    );
    fireEvent.click(screen.getByRole("button", { name: "执行结果发布" }));
    expect(
      screen
        .getByRole("button", { name: "示例组合已完成" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getByText("发布记录 C-001 · 保留来源 B-001")).toBeTruthy();
  });
  it("preserves the first module result when the second denies access and does not run the third", () => {
    render(<CompositionAnalysis />);
    fireEvent.click(screen.getByRole("button", { name: "串联示例" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: "模拟当前用户没有整理权限" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "执行资料登记" }));
    fireEvent.click(screen.getByRole("button", { name: "执行内容整理" }));
    expect(screen.getByRole("status").textContent).toContain(
      "登记结果保留，发布步骤没有执行",
    );
    expect(document.querySelector(".ca-run")?.textContent).toContain(
      "已完成 1 / 3 步",
    );
    expect(screen.queryByRole("button", { name: "执行结果发布" })).toBeNull();
    expect(screen.getByText("资料编号 A-001 · 第 1 版")).toBeTruthy();
  });
});
