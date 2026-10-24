import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  Database,
  Layers3,
  LayoutTemplate,
  MessageCircle,
  RotateCcw,
} from "lucide-react";
import {
  CHAPTERS,
  EXECUTION_STEPS,
  executionView,
  labFlow,
  LIFECYCLE,
  LIFE_SOURCES,
  MAIN_PARTS,
  MODULE_PARTS,
  OVERVIEW,
  RULES,
} from "./reference";
import type { Part, Scenario } from "./reference";
import CompositionAnalysis from "./CompositionAnalysis";
import "../guide/guide.css";
import "./developer-guide.css";

const STORAGE_KEY = "duo-framework-structure-v1";
function initialChapter() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (
      Number.isInteger(value?.chapter) &&
      value.chapter >= 0 &&
      value.chapter < CHAPTERS.length
    )
      return value.chapter;
  } catch {
    /* Browsing remains available without storage. */
  }
  return 0;
}
function Sources({ paths }: { paths: string[] }) {
  return (
    <details className="ar-sources">
      <summary>
        查看对应文件
        <ChevronDown size={15} />
      </summary>
      <p>相对于 common-platform 目录，以下文件对应本段内容。</p>
      <ul>
        {paths.map((path) => (
          <li key={path}>
            <code>{path}</code>
          </li>
        ))}
      </ul>
    </details>
  );
}
function Heading({
  number,
  title,
  text,
}: {
  number: number;
  title: string;
  text: string;
}) {
  return (
    <div className="ar-heading">
      <span className="fg-kicker">
        {number + 1} / {CHAPTERS.length} · {CHAPTERS[number]}
      </span>
      <h1>{title}</h1>
      <p>{text}</p>
    </div>
  );
}
function Detail({ part, label }: { part: Part; label: string }) {
  return (
    <section className="ar-detail" aria-label={label} aria-live="polite">
      <span className="ar-eyebrow">{part.summary}</span>
      <h2>{part.title}</h2>
      <p>{part.does}</p>
      <dl>
        <div>
          <dt>接收什么</dt>
          <dd>{part.receives}</dd>
        </div>
        <div>
          <dt>交回什么</dt>
          <dd>{part.returns}</dd>
        </div>
        <div>
          <dt>保存什么</dt>
          <dd>{part.keeps}</dd>
        </div>
      </dl>
      <p className="ar-example">
        <strong>举个例子</strong>
        {part.example}
      </p>
      <Sources paths={part.sources} />
    </section>
  );
}
function Overall({ openAnalysis }: { openAnalysis: () => void }) {
  const [selected, select] = useState("main");
  const part = OVERVIEW.find((item) => item.id === selected)!;
  return (
    <>
      <Heading
        number={0}
        title="先看各部分怎样组成一个整体"
        text="总框架管理各个微服务，统一认证等公共功能，并把不同模块的业务连接起来。各模块公开业务行为、数据与接口；组合后的业务也使用页面和对话。"
      />
      <button className="ca-core-link" onClick={openAnalysis}>
        重点看：模块怎样公开，总框架怎样组织
        <ArrowRight size={17} />
        <small>当前功能、职责分工与使用范围</small>
      </button>
      <div className="ar-overall">
        <div className="ar-construction" aria-label="框架整体结构">
          <button
            className="ar-entry"
            aria-pressed={selected === "entry"}
            onClick={() => select("entry")}
          >
            <span>
              <LayoutTemplate size={20} />
              固定页面
            </span>
            <span>
              <MessageCircle size={20} />
              对话窗口
            </span>
            <small>同一套操作组件，继续同一件事</small>
          </button>
          <div className="ar-connector">
            <ArrowDown size={19} />
            <span>操作请求与结果</span>
          </div>
          <button
            className="ar-layer ar-main-layer"
            aria-pressed={selected === "main"}
            onClick={() => select("main")}
          >
            <strong>总框架</strong>
            <span>微服务管理 · 统一认证与权限 · 功能调用 · 跨模块业务组织</span>
            <small>页面与对话共用业务进展</small>
          </button>
          <button
            className="ar-contract"
            aria-pressed={selected === "contract"}
            onClick={() => select("contract")}
          >
            <Layers3 size={21} />
            <span>
              <strong>模块框架</strong>
              <small>功能说明、请求结果格式、共用界面接口、记录接口</small>
            </span>
          </button>
          <button
            className="ar-layer ar-business-layer"
            aria-pressed={selected === "business"}
            onClick={() => select("business")}
          >
            <strong>各个业务模块</strong>
            <span className="ca-service-row">
              <span>登记模块</span>
              <ArrowRight size={15} />
              <span>处理模块</span>
              <ArrowRight size={15} />
              <span>发布模块</span>
            </span>
            <small>各自公开业务动作、数据和接口，保留自己的正式记录</small>
          </button>
          <button
            className="ar-records"
            aria-pressed={selected === "records"}
            onClick={() => select("records")}
          >
            <Database size={19} />
            <span>平台记录</span>
            <span>正式业务记录</span>
          </button>
          <p className="ar-caption">
            中间的“模块框架”提供共同约定与连接工具，随各个业务模块一起使用。
          </p>
        </div>
        <Detail part={part} label="整体部分说明" />
      </div>
      <p className="ar-takeaway">
        核心关系：
        <strong>
          模块公开 → 总框架检查与组织 → 各模块处理 → 页面和对话共用结果。
        </strong>
      </p>
    </>
  );
}
function Parts({ kind }: { kind: "main" | "module" }) {
  const parts = kind === "main" ? MAIN_PARTS : MODULE_PARTS;
  const [selected, select] = useState(parts[0].id);
  const [mode, setMode] = useState<"tool" | "workflow">("workflow");
  const part = parts.find((item) => item.id === selected)!;
  return (
    <>
      <Heading
        number={kind === "main" ? 1 : 2}
        title={
          kind === "main"
            ? "主框架把哪些事情集中做好？"
            : "模块框架怎样把业务接到整体中？"
        }
        text={
          kind === "main"
            ? "总框架统一管理微服务、公共功能和模块之间的业务连接。选择下面九个部分，查看职责与当前实现边界。"
            : "它是一套共同格式和连接工具。业务模块按这些约定公开行为、数据、接口与共用界面，供总框架登记、调用和组织。"
        }
      />
      {kind === "module" && (
        <div className="ar-module-mode">
          <div className="ar-switch" role="group" aria-label="选择模块形态">
            <button
              aria-pressed={mode === "tool"}
              onClick={() => setMode("tool")}
            >
              一次处理的工具
            </button>
            <button
              aria-pressed={mode === "workflow"}
              onClick={() => setMode("workflow")}
            >
              持续办理的模块
            </button>
          </div>
          <p>
            {mode === "tool"
              ? "例如整理一段文字：功能说明 → 操作界面 → 处理函数 → 结果。需要共享表单时使用单组件定义，不一定需要正式记录与多步骤流程。"
              : "例如办理一条记录：在功能与共用界面之外，增加正式记录接口、步骤安排和状态规则，才能在多个入口持续办理。权限按实际业务定义。"}
          </p>
        </div>
      )}
      <div className="ar-parts">
        <nav aria-label={kind === "main" ? "主框架组成" : "模块框架组成"}>
          {parts.map((item, index) => (
            <button
              key={item.id}
              aria-pressed={selected === item.id}
              onClick={() => select(item.id)}
            >
              <span className="ar-part-index">{index + 1}</span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.summary}</small>
              </span>
              <ArrowRight size={15} />
            </button>
          ))}
        </nav>
        <Detail
          part={part}
          label={kind === "main" ? "主框架部分说明" : "模块框架部分说明"}
        />
      </div>
      {kind === "module" && (
        <div className="ar-example-strip">
          <strong>以项目自带的事项模块为例</strong>
          <span>
            共用编辑界面 → 提交按钮 → 已登记的提交功能 → 模块检查与保存 →
            最新记录。
          </span>
          <p>
            界面、步骤、后端功能按编号和版本连接。展开对应文件可以看到具体名称与位置。
          </p>
          <details className="ar-sources">
            <summary>
              查看这个例子的连接名称
              <ChevronDown size={15} />
            </summary>
            <p>
              Editor 组件 → flow.json 的 submit 动作 → manifest.json 的
              framework-lab.submit 功能 → handlers.py 的 submit_item 函数。
            </p>
          </details>
        </div>
      )}
    </>
  );
}
function Execution() {
  const [step, setStep] = useState(0);
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [remote, setRemote] = useState(false);
  const [surface, setSurface] = useState("page");
  const view = executionView(step, scenario);
  const current = EXECUTION_STEPS[step];
  return (
    <>
      <Heading
        number={3}
        title="按下“提交”以后，发生了什么？"
        text="从一个已保存的草稿开始，逐步跟随同一次请求。选择不同情况，查看主框架和模块各在哪里处理。"
      />
      <div className="ar-demo-note">
        本页使用示例数据讲解执行顺序，不发送正式业务请求。
      </div>
      <div className="ar-execution-options">
        <div className="ar-switch" role="group" aria-label="选择操作入口">
          <button
            aria-pressed={surface === "page"}
            onClick={() => setSurface("page")}
          >
            <LayoutTemplate size={16} />
            从页面操作
          </button>
          <button
            aria-pressed={surface === "conversation"}
            onClick={() => setSurface("conversation")}
          >
            <MessageCircle size={16} />
            从对话操作
          </button>
        </div>
        <label>
          演示情况
          <select
            value={scenario}
            onChange={(event) => {
              const value = event.target.value as Scenario;
              setScenario(value);
              setStep(0);
              if (value === "timeout") setRemote(true);
            }}
          >
            <option value="normal">正常提交</option>
            <option value="permission">没有办理权限</option>
            <option value="version">内容版本已经变化</option>
            <option value="repeat">相同请求再次发送</option>
            <option value="timeout">独立服务返回超时</option>
          </select>
        </label>
      </div>
      <div className="ar-execution-layout">
        <ol className="ar-route" aria-label="请求经过的部分">
          {EXECUTION_STEPS.map((item, index) => (
            <li
              key={item.title}
              className={
                index === step ? "current" : index < step ? "passed" : ""
              }
              aria-current={index === step ? "step" : undefined}
            >
              <span>{index < step ? <Check size={14} /> : index + 1}</span>
              <div>
                <strong>{item.title}</strong>
                <small>
                  {index === 0 || index === 7
                    ? surface === "page"
                      ? "固定页面"
                      : "对话窗口"
                    : item.owner}
                </small>
              </div>
            </li>
          ))}
        </ol>
        <section className="ar-execution-detail" aria-live="polite">
          <span className="ar-eyebrow">
            第 {step + 1} 步 · {current.owner}
          </span>
          <h2>{current.title}</h2>
          <p>{current.detail}</p>
          <div
            className="ar-switch ar-location"
            role="group"
            aria-label="选择模块运行位置"
          >
            <button
              aria-pressed={!remote}
              disabled={scenario === "timeout"}
              onClick={() => setRemote(false)}
            >
              本地 Python 模块
            </button>
            <button aria-pressed={remote} onClick={() => setRemote(true)}>
              独立服务
            </button>
          </div>
          <p className="ar-connection-note">
            {remote
              ? "主框架在服务端通过 HTTP 发送当前身份与请求上下文；业务数据由独立服务保存。浏览器不取得服务连接凭据。"
              : "主框架调用已安装的处理函数；本地示例用框架提供的数据库会话保存变化。"}
          </p>
          <div
            className="ar-state-values"
            role="group"
            aria-label="这次演示的三种状态"
          >
            <div>
              <small>正式事项状态</small>
              <strong>{view.business}</strong>
              <span>{view.revision} · 业务模块负责</span>
            </div>
            <div>
              <small>个人查看步骤</small>
              <strong>{view.cursor}</strong>
              <span>主框架保存</span>
            </div>
            <div>
              <small>本次执行结果</small>
              <strong>{view.result}</strong>
              <span>调用记录负责</span>
            </div>
          </div>
          {view.message && (
            <p
              className={
                "ar-outcome " +
                (scenario === "normal" || scenario === "repeat" ? "good" : "")
              }
              role="status"
            >
              {view.message}
            </p>
          )}
          {scenario === "repeat" && step < 3 && (
            <p className="ar-caption">
              本例预先已有一次相同请求成功，正式记录从开始就是已提交。
            </p>
          )}
          <div className="ar-demo-controls">
            <button
              className="ar-secondary"
              onClick={() => setStep(0)}
              disabled={step === 0}
            >
              <RotateCcw size={15} />
              重新查看
            </button>
            <button
              className="ar-primary"
              disabled={view.ended}
              onClick={() => setStep((value) => Math.min(value + 1, view.stop))}
            >
              {view.ended
                ? "这次过程已结束"
                : `下一步：${EXECUTION_STEPS[step + 1].title}`}
              <ArrowRight size={16} />
            </button>
          </div>
          <details className="ar-payload">
            <summary>
              请求和返回内容是什么？
              <ChevronDown size={15} />
            </summary>
            <div>
              <h3>提交携带的主要信息</h3>
              <dl>
                <div>
                  <dt>操作</dt>
                  <dd>submit，对应 framework-lab.submit</dd>
                </div>
                <div>
                  <dt>记录引用</dt>
                  <dd>记录类型和编号，用于找到同一件事</dd>
                </div>
                <div>
                  <dt>两种版本</dt>
                  <dd>个人流程版本与正式内容版本分别检查</dd>
                </div>
                <div>
                  <dt>请求编号</dt>
                  <dd>识别是否重复发送同一次操作</dd>
                </div>
              </dl>
              <h3>模块交回的主要信息</h3>
              <p>
                处理说明，以及记录引用、正式状态、内容版本、显示数据和允许动作。读取最新记录后再更新界面。
              </p>
            </div>
          </details>
          <Sources paths={current.sources} />
        </section>
      </div>
    </>
  );
}
function Rules() {
  const [selected, setSelected] = useState("states");
  const [cursor, setCursor] = useState("填写内容");
  const [state, setState] = useState("draft");
  const [lastAction, setLastAction] = useState("尚未执行业务动作");
  const rule = RULES.find((item) => item.id === selected)!;
  const transitions = labFlow.state_machine.transitions.filter(
    (item) => item.from === state && item.from !== item.to,
  );
  return (
    <>
      <Heading
        number={4}
        title="哪些规则让各部分保持一致？"
        text="同一件事能从不同入口继续，依靠明确的状态、权限、版本和保存规则。选择一条，配合例子理解。"
      />
      <div className="ar-rule-layout">
        <nav className="ar-rule-nav" aria-label="选择交互规则">
          {RULES.map((item) => (
            <button
              key={item.id}
              aria-pressed={selected === item.id}
              onClick={() => setSelected(item.id)}
            >
              {item.title}
              <ArrowRight size={15} />
            </button>
          ))}
        </nav>
        <section className="ar-rule-detail" aria-live="polite">
          <span className="ar-eyebrow">{rule.summary}</span>
          <h2>{rule.title}</h2>
          <p>{rule.explanation}</p>
          <p className="ar-example">
            <strong>举个例子</strong>
            {rule.example}
          </p>
          <Sources paths={rule.sources} />
        </section>
      </div>
      <section className="ar-rule-lab">
        <div>
          <span className="ar-eyebrow">动手看区别 · 只改变本页示例</span>
          <h2>切换个人步骤，会改变正式状态吗？</h2>
          <p>
            先只切换步骤，再点正式操作，观察结果分别怎样变化。本例模拟本人完整办理，用来说明状态关系。
          </p>
        </div>
        <div className="ar-rule-workspace">
          <div>
            <h3>个人查看步骤</h3>
            <div className="ar-switch" role="group" aria-label="切换个人步骤">
              {["填写内容", "处理记录"].map((value) => (
                <button
                  key={value}
                  aria-pressed={cursor === value}
                  onClick={() => setCursor(value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <p>
              现在看：<strong>{cursor}</strong>
            </p>
          </div>
          <div>
            <h3>正式事项状态</h3>
            <div className="ar-state-graph" aria-label="示例状态变化图">
              {labFlow.state_machine.states.map((item) => (
                <span
                  key={item.id}
                  className={state === item.id ? "active" : ""}
                  aria-current={state === item.id ? "true" : undefined}
                >
                  {item.title}
                </span>
              ))}
            </div>
            <div className="ar-action-buttons">
              {transitions.map((item) => (
                <button
                  key={item.action_id}
                  onClick={() => {
                    setState(item.to);
                    setLastAction(`${item.title}处理成功`);
                  }}
                >
                  {item.title}
                </button>
              ))}
              <button
                className="ar-text-button"
                onClick={() => {
                  setState("draft");
                  setCursor("填写内容");
                  setLastAction("尚未执行业务动作");
                }}
              >
                重置示例
              </button>
            </div>
            <p role="status">
              {lastAction}。
              {state === "completed"
                ? "已完成，没有下一项正式操作。"
                : "只有正式操作才改变这里的状态。"}
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
function Lifecycle() {
  const [stage, setStage] = useState(0);
  const [feature, setFeature] = useState<"current" | "configured" | "outside">(
    "current",
  );
  return (
    <>
      <Heading
        number={5}
        title="从加载到恢复，框架怎样运行？"
        text="模块能否使用，要同时看安装、启用、连接情况和实际授权。保存的记录用于恢复，不代替重新检查。"
      />
      <div className="ar-life">
        <nav aria-label="模块运行阶段">
          {LIFECYCLE.map((item, index) => (
            <button
              key={item.title}
              aria-pressed={stage === index}
              onClick={() => setStage(index)}
            >
              <span>{index + 1}</span>
              {item.title}
              <ArrowRight size={15} />
            </button>
          ))}
        </nav>
        <section aria-live="polite">
          <h2>{LIFECYCLE[stage].title}</h2>
          <p>{LIFECYCLE[stage].text}</p>
          <p className="ar-outcome good">{LIFECYCLE[stage].outcome}</p>
        </section>
      </div>
      <Sources paths={LIFE_SOURCES} />
      <div className="ar-storage">
        <div>
          <Database size={20} />
          <h2>主框架保存</h2>
          <p>
            用户与授权、会话与消息、个人步骤与草稿、调用回执和后台执行记录。当前参考实现使用本地
            SQLite。
          </p>
        </div>
        <div>
          <Database size={20} />
          <h2>业务模块保存</h2>
          <p>
            正式记录、状态、内容版本和历史。本地模块可共享同一数据库；独立服务保存自己的记录、文件和任务。
          </p>
        </div>
      </div>
      <section className="ar-features">
        <h2>目前有哪些功能，哪些还需要另外准备？</h2>
        <div className="ar-switch" role="group" aria-label="查看功能范围">
          <button
            aria-pressed={feature === "current"}
            onClick={() => setFeature("current")}
          >
            框架已有
          </button>
          <button
            aria-pressed={feature === "configured"}
            onClick={() => setFeature("configured")}
          >
            按模块配置
          </button>
          <button
            aria-pressed={feature === "outside"}
            onClick={() => setFeature("outside")}
          >
            当前没有
          </button>
        </div>
        <div aria-live="polite">
          {feature === "current" ? (
            <p>
              本地登录与会话、范围授权、功能目录、本地与远程调用、共享组件、个人流程、对话恢复与分支、运行记录及调试观察。
            </p>
          ) : feature === "configured" ? (
            <p>
              专业业务规则、记录读取、文件存储和业务长任务由模块提供；独立服务连接和模型服务需要实际配置。前端组件以已安装的
              React/TypeScript 包使用。
            </p>
          ) : (
            <p>
              当前没有自动跨服务事务与回滚、任意模块之间自动理解数据含义的转换、通用拖拽流程编辑器。游客调用、外部统一登录和多机高可用仍需另外实现与验证。
            </p>
          )}
        </div>
      </section>
      <div className="ar-real-example">
        <BookOpen size={24} />
        <div>
          <h2>在实际工作台观察同样的关系</h2>
          <p>
            通用工作项实验室可以创建、提交、退回、修改并完成记录。它是本人操作的中性示例；正式多人审批规则由相应业务模块定义。
          </p>
          <div>
            <a href="#flows">
              打开事项示例
              <ArrowUpRight size={16} />
            </a>
            <a href="#devtools">
              打开调试台
              <ArrowUpRight size={16} />
            </a>
          </div>
        </div>
      </div>
      <details className="ar-sources">
        <summary>
          继续阅读详细说明
          <ChevronDown size={15} />
        </summary>
        <div className="ar-reading">
          <a href="/reports/handbooks/architecture-core.html">
            整体架构与代码地图
            <ArrowUpRight size={15} />
          </a>
          <a href="/reports/handbooks/module-debug.html">
            模块接口与调试说明
            <ArrowUpRight size={15} />
          </a>
        </div>
      </details>
    </>
  );
}
export default function DeveloperGuide() {
  const [chapter, setChapter] = useState(initialChapter);
  const contentRef = useRef<HTMLElement>(null);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ chapter }));
    } catch {
      /* Reading can continue without storage. */
    }
  }, [chapter]);
  function navigate(next: number) {
    setChapter(next);
    requestAnimationFrame(() => {
      contentRef.current?.focus();
      contentRef.current?.scrollIntoView({ block: "start" });
    });
  }
  return (
    <div className="framework-guide developer-guide">
      <a
        className="fg-skip"
        href="#structure-content"
        onClick={(event) => {
          event.preventDefault();
          contentRef.current?.focus();
        }}
      >
        跳到正文
      </a>
      <header className="fg-header">
        <a
          className="fg-brand"
          href="#developer-guide"
          onClick={() => navigate(0)}
        >
          <span className="fg-brand-symbol">
            <Layers3 size={23} />
          </span>
          <span>
            框架全貌<small>构造、分工与交互规则</small>
          </span>
        </a>
        <div className="ar-header-links">
          <a href="#guide">
            用户指南
            <ArrowUpRight size={15} />
          </a>
          <a href="#flows">
            打开工作台
            <ArrowUpRight size={15} />
          </a>
        </div>
      </header>
      <nav className="fg-chapters ar-chapters" aria-label="框架讲解章节">
        {CHAPTERS.map((title, index) => (
          <button
            key={title}
            aria-current={chapter === index ? "step" : undefined}
            aria-label={`${index + 1} ${title}`}
            onClick={() => navigate(index)}
          >
            <span>{index + 1}</span>
            {title}
          </button>
        ))}
      </nav>
      <main
        className="fg-main ar-content"
        id="structure-content"
        tabIndex={-1}
        ref={contentRef}
      >
        {chapter === 0 ? (
          <Overall openAnalysis={() => navigate(6)} />
        ) : chapter === 1 ? (
          <Parts key="main" kind="main" />
        ) : chapter === 2 ? (
          <Parts key="module" kind="module" />
        ) : chapter === 3 ? (
          <Execution />
        ) : chapter === 4 ? (
          <Rules />
        ) : chapter === 5 ? (
          <Lifecycle />
        ) : (
          <CompositionAnalysis />
        )}
      </main>
      <footer className="ar-footer">
        <button
          className="ar-secondary"
          disabled={chapter === 0}
          onClick={() => navigate(chapter - 1)}
        >
          <ArrowLeft size={16} />
          上一章
        </button>
        <span>
          {chapter + 1} / {CHAPTERS.length} · {CHAPTERS[chapter]}
        </span>
        <button
          className="ar-primary"
          onClick={() =>
            navigate(chapter === CHAPTERS.length - 1 ? 0 : chapter + 1)
          }
        >
          {chapter === CHAPTERS.length - 1
            ? "回到整体构造"
            : `继续看${CHAPTERS[chapter + 1]}`}
          <ArrowRight size={16} />
        </button>
      </footer>
    </div>
  );
}
