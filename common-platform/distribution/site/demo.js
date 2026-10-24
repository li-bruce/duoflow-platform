/* A local teaching example. Formal platform state and calls live in the app. */
const product = document.body.dataset.product;
const isKit = product === "kit";
const steps = isKit
  ? [
      ["描述业务", "写下这个模块要完成的事。"],
      ["看清业务图", "把输入、动作和结果放在一起，核对它们的关系。"],
      ["形成模块", "沿着已经明确的业务，准备功能和两种办理入口。"],
      ["体验两种入口", "在页面和对话之间切换，查看相同内容与结果。"],
      ["查看检查结果", "逐项查看模块公开内容与接入检查的演示结果。"],
    ]
  : [
      ["创建事项", "保存一项简单工作，开始体验。"],
      ["在对话中继续", "切换入口，继续查看刚才的内容。"],
      ["提交办理", "提交后，事项进入下一办理阶段。"],
      ["处理反馈", "收到反馈后修改内容，再次提交。"],
      ["完成与恢复", "查看完成状态和保留下来的办理记录。"],
      ["连接另一个模块", "选择下一项业务，将已有结果交给它继续处理。"],
      ["查看整体业务图", "沿着同一张图，查看每一步与数据去向。"],
    ];
let index = 0;
let mode = "page";
let name = isKit ? "文字整理" : "我的第一项工作";
let text = "把一段文字整理为清楚的结果。";
const byId = (id) => document.getElementById(id);
const storageKey = `duoflow-intro-demo-v0.02-${product}`;

// Retain teaching data under a separate key for each product.
function saveState() {
  try {
    localStorage.setItem(
      storageKey,
      JSON.stringify({ index, mode, name, text }),
    );
  } catch {
    // Storage availability does not prevent in-page interaction.
  }
}

try {
  const stored = JSON.parse(localStorage.getItem(storageKey) || "null");
  if (
    stored &&
    Number.isInteger(stored.index) &&
    stored.index >= 0 &&
    stored.index < steps.length &&
    ["page", "conversation"].includes(stored.mode) &&
    typeof stored.name === "string" &&
    stored.name.length <= 60 &&
    typeof stored.text === "string" &&
    stored.text.length <= 300
  ) {
    ({ index, mode, name, text } = stored);
  }
} catch {
  // Start from the initial example if the saved teaching record is damaged.
}

// User-entered values are always added with textContent, never interpolated HTML.
function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function record(stage) {
  const card = element("div", undefined, "demo-card");
  card.append(element("span", mode === "page" ? "页面" : "对话", "caption"));
  card.append(element("strong", name));
  card.append(element("p", text));
  const status = isKit
    ? "共用内容 · 共用结果"
    : [
        "草稿已保存",
        "草稿已保存",
        "已提交",
        "修改后已提交",
        "已完成",
        "结果已传递",
        "组合已完成",
      ][index];
  card.append(element("span", status, "demo-status"));
  stage.append(card);
}

function modeControls(stage) {
  const toggle = element("div", undefined, "mode-toggle");
  for (const [id, label] of [
    ["page", "页面"],
    ["conversation", "对话"],
  ]) {
    const button = element("button", label, "small-button");
    button.setAttribute("aria-pressed", String(mode === id));
    button.addEventListener("click", () => {
      mode = id;
      render();
    });
    toggle.append(button);
  }
  stage.append(toggle);
  record(stage);
}

function render() {
  saveState();
  byId("demo-category").textContent = isKit
    ? "duoflow-module Skill · 使用过程示意"
    : "DuoFlow Platform · 办理过程示意";
  byId("demo-title").textContent = isKit
    ? "从一句需求，到一个业务模块。"
    : "从页面开始，在对话里继续。";
  byId("demo-description").textContent = isKit
    ? "跟随五个步骤，体验业务整理、模块构建和检查的主要特性。"
    : "跟随同一项工作的内容和状态，体验两种入口怎样配合。";
  byId("step-list").replaceChildren();
  steps.forEach(([title], position) => {
    const button = element("button", undefined, "step-button");
    button.append(
      element("span", String(position + 1).padStart(2, "0")),
      document.createTextNode(title),
    );
    if (position === index) button.setAttribute("aria-current", "step");
    button.addEventListener("click", () => {
      index = position;
      render();
    });
    byId("step-list").append(button);
  });
  byId("step-caption").textContent =
    `第 ${index + 1} 步，共 ${steps.length} 步`;
  byId("step-title").textContent = steps[index][0];
  byId("step-description").textContent = steps[index][1];
  byId("previous").disabled = index === 0;
  byId("next").textContent =
    index === steps.length - 1 ? "再体验一次 ↻" : "继续 →";
  byId("feedback").textContent = "";
  const stage = byId("stage");
  stage.replaceChildren();
  if (index === 0) {
    const label = element("label", isKit ? "模块名称" : "事项名称");
    const input = element("input");
    input.maxLength = 60;
    input.value = name;
    input.addEventListener("input", () => {
      name = input.value;
      saveState();
    });
    label.append(input);
    const details = element("label", "内容说明");
    const area = element("textarea");
    area.maxLength = 300;
    area.value = text;
    area.addEventListener("input", () => {
      text = area.value;
      saveState();
    });
    details.append(area);
    stage.append(label, details);
  } else if (isKit && index === 1) {
    const graph = element("div", undefined, "demo-flow");
    for (const label of ["输入文字", "→", "整理内容", "→", "查看结果"])
      graph.append(element(label === "→" ? "b" : "span", label));
    stage.append(
      graph,
      element("p", `围绕「${name}」，把动作与结果逐一对应。`, "demo-summary"),
    );
  } else if (isKit && index === 2) {
    const list = element("ul", undefined, "check-list");
    for (const label of [
      "组件编号与业务位置",
      "输入、结果与事件",
      "局部状态与正式状态",
      "页面与对话入口",
      "完整流程与部分功能",
    ])
      list.append(element("li", "✓ " + label));
    stage.append(
      list,
      element(
        "p",
        "模块围绕同一项业务，提供共同的内容和办理规则。",
        "demo-summary",
      ),
    );
  } else if (isKit && index === 4) {
    const list = element("ul", undefined, "check-list");
    for (const label of [
      "公开功能",
      "数据与权限",
      "身份验证",
      "服务状态",
      "调用接口",
      "模块确认",
      "办理入口",
      "运行状态回传",
    ])
      list.append(element("li", "✓ " + label));
    stage.append(
      list,
      element(
        "p",
        "这里展示检查项目。正式接入时，各项结果来自实际检查。",
        "demo-summary",
      ),
    );
  } else if (!isKit && index >= 5) {
    const graph = element("div", undefined, "demo-flow");
    for (const label of [
      "记录模块 · 已完成",
      "→",
      "记录编号",
      "→",
      "接收模块 · 已完成",
    ])
      graph.append(element(label === "→" ? "b" : "span", label));
    stage.append(graph);
    stage.append(
      element(
        "p",
        index === 5
          ? "选择“接收记录”作为下一步，连接记录模块给出的编号。每一步都核对当前办理人的权限。"
          : "图中保留两个模块的关系。点击下面的节点，查看它接收什么、完成了什么。",
        "demo-summary",
      ),
    );
    const detail = element("p", "请选择一个节点。", "demo-summary");
    const nodes = element("div", undefined, "mode-toggle");
    for (const [title, description] of [
      ["记录模块", `已完成「${name}」，提供记录编号。`],
      ["数据连接", "把记录编号传给接收模块，来源和目标分别核对权限。"],
      ["接收模块", "读取指定记录，保存本步结果，组合完成。"],
    ]) {
      const button = element("button", title, "small-button");
      button.addEventListener("click", () => {
        detail.textContent = description;
      });
      nodes.append(button);
    }
    stage.append(nodes, detail);
  } else {
    modeControls(stage);
    if (!isKit && index === 3)
      stage.append(
        element(
          "p",
          "反馈示例：请补充内容说明。修改完成后再次提交，保留前后的办理记录。",
          "demo-summary",
        ),
      );
    if (!isKit && index === 4)
      stage.append(
        element("p", "创建 → 对话续办 → 提交 → 修改 → 完成", "demo-summary"),
      );
  }
}

byId("previous").addEventListener("click", () => {
  if (index > 0) {
    index--;
    render();
  }
});
byId("next").addEventListener("click", () => {
  if (!name.trim()) {
    byId("feedback").textContent = "请先填写名称。";
    return;
  }
  index = (index + 1) % steps.length;
  render();
});
byId("reset").addEventListener("click", () => {
  index = 0;
  mode = "page";
  name = isKit ? "文字整理" : "我的第一项工作";
  text = "把一段文字整理为清楚的结果。";
  render();
});
render();
