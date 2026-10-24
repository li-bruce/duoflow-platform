import IdentityManagement from "./IdentityManagement";
import { isPlatformAdmin } from "./types";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  BookOpen,
  Boxes,
  Braces,
  Check,
  ChevronRight,
  CircleHelp,
  Code2,
  FileCheck2,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plug,
  RefreshCw,
  Server,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api, post, setSession } from "./api";
import type { ModelProfile, Registry, Session, User } from "./types";
import { Empty, ErrorNotice, Loading, PrettyData, SectionHeading } from "./ui";
import Workbench from "./Workbench";
import ModuleManagement from "./ModuleManagement";
import ModelSettings from "./ModelSettings";
import RemoteServices from "./RemoteServices";
import type { UIContext } from "./ResultView";
import Conversations from "./Conversations";
import Records from "./Records";
import ErrorBoundary from "./ErrorBoundary";
import { SurfaceSummary } from "./ModuleStatus";
import InteractionWorkbench from "./interactions/InteractionWorkbench";
import FlowWorkbench from "./workflows/FlowWorkbench";
import MyBusiness from "./MyBusiness";
import ModuleHub from "./business/ModuleHub";
import CompositionWorkbench from "./compositions/CompositionWorkbench";
import AccessManagement from "./AccessManagement";
import DevTools from "./DevTools";
import "./styles.css";
import "./moduleStyles.css";

type Page =
  | "compositions"
  | "module-hub"
  | "devtools"
  | "business"
  | "access"
  | "flows"
  | "components"
  | "intro"
  | "workbench"
  | "chat"
  | "records"
  | "models"
  | "services"
  | "modules"
  | "contracts"
  | "identity";
const pages: {
  id: Page;
  label: string;
  icon: LucideIcon;
  description: string;
  admin?: boolean;
}[] = [
  {
    id: "compositions",
    label: "组合业务",
    icon: Workflow,
    description: "连接模块业务并查看整体办理过程",
  },
  {
    id: "module-hub",
    label: "模块业务",
    icon: Boxes,
    description: "选择模块公开的业务并开始办理",
  },
  {
    id: "devtools",
    label: "开发调试台",
    icon: Code2,
    description: "检查注册声明并观察真实状态机",
    admin: true,
  },
  {
    id: "business",
    label: "我的业务",
    icon: Workflow,
    description: "继续办理与跨角色业务目录",
  },
  {
    id: "access",
    label: "业务授权",
    icon: ShieldCheck,
    description: "分配模块职责与业务范围",
    admin: true,
  },
  {
    id: "flows",
    label: "步骤工作台",
    icon: Workflow,
    description: "共享业务对象与分步办理",
  },
  {
    id: "components",
    label: "组件工作台",
    icon: Boxes,
    description: "共享组件与持久交互",
  },
  {
    id: "intro",
    label: "使用说明",
    icon: BookOpen,
    description: "从这里了解共用能力",
  },
  {
    id: "workbench",
    label: "固定页面",
    icon: LayoutDashboard,
    description: "用熟悉的表单完成工作",
  },
  {
    id: "chat",
    label: "统一对话",
    icon: MessageSquare,
    description: "在对话中调用相同能力",
  },
  {
    id: "records",
    label: "运行与记录",
    icon: Activity,
    description: "任务、资源与处理轨迹",
  },
  {
    id: "models",
    label: "模型配置",
    icon: Settings2,
    description: "管理模型连接与密钥",
    admin: true,
  },
  {
    id: "modules",
    label: "模块管理",
    icon: Boxes,
    description: "管理模块、页面工具与对话能力",
    admin: true,
  },
  {
    id: "services",
    label: "服务接入",
    icon: Plug,
    description: "接入独立部署的远程能力",
    admin: true,
  },
  {
    id: "identity",
    label: "用户与权限",
    icon: ShieldCheck,
    description: "统一管理账号、角色、机构和团队",
    admin: true,
  },
  {
    id: "contracts",
    label: "模块契约",
    icon: Braces,
    description: "查看已注册能力与扩展方式",
  },
];
function initialPage(): Page {
  const value = location.hash.slice(1).split("?")[0];
  return pages.some((p) => p.id === value) ? (value as Page) : "intro";
}
const emptyRegistry: Registry = {
  modules: [],
  capabilities: [],
  renderers: [],
};

function Login({
  onLogin,
  message,
}: {
  onLogin: (session: Session) => void;
  message: string;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function login() {
    setBusy(true);
    setError(null);
    try {
      const session = await post<Session>("/auth/login", {
        username,
        password,
      });
      setSession(session);
      onLogin(session);
      location.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <Boxes size={22} />
          </span>
          <div>
            <strong>共用能力工作台</strong>
            <small>COMMON CAPABILITY WORKSPACE</small>
          </div>
        </div>
        <div className="login-story-body">
          <span className="eyebrow light">通用框架 · 最小功能演示</span>
          <h1>
            同一份能力，
            <br />
            两种自然的用法。
          </h1>
          <p>
            用页面完成工作，也能留在对话里。
            <br />
            表单、任务、结果与记录始终相连。
          </p>
          <div className="login-diagram">
            <div>
              <Braces size={20} />
              <span>注册能力</span>
            </div>
            <ArrowRight size={19} />
            <div className="login-diagram-branches">
              <span>
                <LayoutDashboard size={17} />
                固定页面
              </span>
              <span>
                <MessageSquare size={17} />
                统一对话
              </span>
            </div>
          </div>
        </div>
        <div className="login-footnote">
          <ShieldCheck size={16} />
          本机运行 · 独立账号 · 过程可追溯
        </div>
      </div>
      <div className="login-panel">
        <form
          className="login-form"
          onSubmit={(e) => {
            e.preventDefault();
            void login();
          }}
        >
          <div className="eyebrow">欢迎回来</div>
          <h2>登录工作台</h2>
          <p className="login-subtitle">
            选择演示身份，体验一项能力的完整过程。
          </p>
          {message && <div className="notice warning">{message}</div>}
          <label className="field">
            账号
            <input
              autoFocus
              required
              autoComplete="username"
              placeholder="输入账号"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="field">
            密码
            <input
              required
              autoComplete="current-password"
              type="password"
              placeholder="输入密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <ErrorNotice error={error} />
          <button className="button primary full login-submit" disabled={busy}>
            {busy ? (
              <Loading label="正在登录…" />
            ) : (
              <>
                进入工作台
                <ArrowRight size={17} />
              </>
            )}
          </button>
          <div className="demo-accounts">
            <div className="demo-account-title">
              <CircleHelp size={15} />
              本机演示账号
            </div>
            <button
              type="button"
              onClick={() => {
                setUsername("admin");
                setPassword("demo-admin-2026");
              }}
            >
              <span>
                <strong>管理员</strong>
                <small>配置模型、执行能力、处理审核</small>
              </span>
              <span>
                <code>admin</code>
                <ChevronRight size={15} />
              </span>
            </button>
            <button
              type="button"
              onClick={() => {
                setUsername("member");
                setPassword("demo-member-2026");
              }}
            >
              <span>
                <strong>成员</strong>
                <small>执行能力、对话、提交审核</small>
              </span>
              <span>
                <code>member</code>
                <ChevronRight size={15} />
              </span>
            </button>
            <p>点击身份可填入演示账号和密码。演示密码仅用于本机环境。</p>
          </div>
        </form>
      </div>
    </div>
  );
}

function Intro({
  registry,
  profiles,
  navigate,
  user,
}: {
  registry: Registry;
  profiles: ModelProfile[];
  navigate: (page: Page) => void;
  user: User;
}) {
  return (
    <>
      <p>
        <a className="button secondary" href="#guide">
          <BookOpen size={16} />
          打开交互式入门指南
        </a>
      </p>
      <SectionHeading
        eyebrow="开始使用"
        title="把能力连接起来"
        description="先走通一个小流程，再把它变成可扩展的工作方式。"
      />
      <section className="hero">
        <div className="hero-copy">
          <span className="hero-label">
            <span />
            可运行的通用框架
          </span>
          <h2>
            一次注册，
            <br />
            页面与对话一起工作。
          </h2>
          <p>
            这里的每个表单、动作和结果都来自能力目录。
            <br />
            你选择交互方式，系统保存同一份工作记录。
          </p>
          <div className="hero-actions">
            <button
              className="button white"
              onClick={() => navigate("workbench")}
            >
              试用第一个能力
              <ArrowRight size={16} />
            </button>
            <button
              className="button ghost-light"
              onClick={() => navigate("chat")}
            >
              <MessageSquare size={16} />
              进入对话
            </button>
          </div>
        </div>
        <div className="hero-diagram">
          <div className="diagram-source">
            <div className="diagram-icon">
              <Braces size={25} />
            </div>
            <strong>能力注册</strong>
            <small>输入 · 执行 · 输出 · 呈现</small>
          </div>
          <div className="diagram-line">
            <span />
            <ArrowDown size={17} />
            <span />
          </div>
          <div className="diagram-targets">
            <div>
              <LayoutDashboard size={20} />
              <strong>固定页面</strong>
              <small>熟悉的操作界面</small>
            </div>
            <div>
              <MessageSquare size={20} />
              <strong>统一对话</strong>
              <small>交互卡片直接办理</small>
            </div>
          </div>
          <div className="diagram-shared">
            <ShieldCheck size={14} />
            共享权限、对象版本与执行结果
          </div>
        </div>
      </section>
      <div className="metrics-row">
        <div>
          <span className="metric-icon">
            <Boxes size={18} />
          </span>
          <strong>{registry.modules.length}</strong>
          <span>已安装模块</span>
        </div>
        <div>
          <span className="metric-icon">
            <Play size={18} />
          </span>
          <strong>{registry.capabilities.length}</strong>
          <span>当前可用能力</span>
        </div>
        <div>
          <span className="metric-icon">
            <Server size={18} />
          </span>
          <strong>{profiles.filter((p) => p.enabled !== false).length}</strong>
          <span>已启用模型连接</span>
        </div>
      </div>
      <div className="subsection-heading">
        <h2>建议从这三个步骤开始</h2>
        <span>约 5 分钟，了解最小闭环</span>
      </div>
      <div className="guide-grid">
        <article className="guide-card">
          <div className="guide-number">01</div>
          <span className="guide-icon">
            <FileCheck2 size={23} />
          </span>
          <h3>先运行一个确定性能力</h3>
          <p>
            在固定页面选择已注册能力、填写表单。查看实际输出、保存的资源和下载文件。
          </p>
          <span className="tag green">基础处理无需模型</span>
          <button className="text-button" onClick={() => navigate("workbench")}>
            打开固定页面
            <ArrowRight size={15} />
          </button>
        </article>
        <article className="guide-card">
          <div className="guide-number">02</div>
          <span className="guide-icon">
            <MessageSquare size={23} />
          </span>
          <h3>在对话里再做一次</h3>
          <p>
            新建会话，展开能力卡片。使用相同表单运行，结果会留在对话历史中。
          </p>
          <span className="tag">共享表单与结果组件</span>
          <button className="text-button" onClick={() => navigate("chat")}>
            打开统一对话
            <ArrowRight size={15} />
          </button>
        </article>
        <article className="guide-card">
          <div className="guide-number">03</div>
          <span className="guide-icon">
            <Sparkles size={23} />
          </span>
          <h3>连接模型，尝试智能处理</h3>
          <p>
            {isPlatformAdmin(user)
              ? "填写 API 地址、模型和密钥，保存后测试连接，再发起真实模型对话或后台任务。"
              : "使用管理员配置的默认模型，发起真实模型对话或后台任务，查看执行结果。"}
          </p>
          <span className="tag violet">按实际配置调用模型</span>
          <button
            className="text-button"
            onClick={() => navigate(isPlatformAdmin(user) ? "models" : "chat")}
          >
            {isPlatformAdmin(user) ? "配置模型连接" : "进入模型对话"}
            <ArrowRight size={15} />
          </button>
        </article>
      </div>
      <p>
        <a
          className="button primary"
          href="/reports/handbooks/"
          target="_blank"
          rel="noreferrer"
        >
          打开功能与模块使用手册 <BookOpen size={16} />
        </a>{" "}
        <a
          className="button secondary"
          href="/reports/"
          target="_blank"
          rel="noreferrer"
        >
          架构与验收报告 <BookOpen size={16} />
        </a>
      </p>
      <section className="workflow-banner">
        <div>
          <Workflow size={22} />
          <div>
            <h3>每一次操作都有去向</h3>
            <p>
              运行会保存记录；产物可以送审；管理员处理后，双方看到同一状态。
            </p>
          </div>
        </div>
        <button
          className="button secondary"
          onClick={() => navigate("records")}
        >
          查看运行与记录
          <ArrowRight size={15} />
        </button>
      </section>
    </>
  );
}

function Contracts({ registry }: { registry: Registry }) {
  const [active, setActive] = useState(registry.modules[0]?.id || "");
  const module =
    registry.modules.find((m) => m.id === active) || registry.modules[0];
  return (
    <>
      <SectionHeading
        eyebrow="MODULE REGISTRY"
        title="模块契约"
        description="把功能声明成能力，固定页面和对话就能共同使用它。"
      />
      <div className="contract-overview">
        <div>
          <span className="contract-number">1</span>
          <h3>定义输入与输出</h3>
          <p>JSON Schema 描述字段、校验和数据结构。</p>
        </div>
        <ArrowRight size={18} />
        <div>
          <span className="contract-number">2</span>
          <h3>登记处理与呈现</h3>
          <p>绑定处理器、权限、执行模式和共享组件。</p>
        </div>
        <ArrowRight size={18} />
        <div>
          <span className="contract-number">3</span>
          <h3>两种入口共同使用</h3>
          <p>页面表单和对话卡片调用同一能力标识。</p>
        </div>
      </div>
      <div className="contract-tabs">
        {registry.modules.map((m) => (
          <button
            className={module?.id === m.id ? "selected" : ""}
            onClick={() => setActive(m.id)}
            key={m.id}
          >
            <Boxes size={16} />
            {m.name}
            <span>v{m.version}</span>
          </button>
        ))}
      </div>
      {module ? (
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>
                {module.name}{" "}
                <span
                  className={
                    "tag " + (module.source === "remote" ? "violet" : "")
                  }
                >
                  {module.source === "core"
                    ? "平台核心"
                    : module.source === "remote"
                      ? "远程模块"
                      : "本机模块"}
                </span>
              </h2>
              <p className="subtle">{module.description}</p>
            </div>
            <code>{module.id}</code>
          </div>
          <SurfaceSummary
            surfaces={module.surfaces}
            capabilities={module.capabilities}
          />
          <div className="capability-contracts">
            {module.capabilities.map((cap) => (
              <details className="contract-item" key={cap.id}>
                <summary>
                  <div>
                    <Code2 size={18} />
                    <strong>{cap.title}</strong>
                    <code>{cap.id}</code>
                  </div>
                  <span className="tag">
                    {cap.execution.mode === "background"
                      ? "后台任务"
                      : "同步能力"}
                  </span>
                  <ChevronRight size={16} />
                </summary>
                <p>{cap.description}</p>
                <div className="contract-bindings">
                  <span>
                    固定页：
                    <code>
                      {cap.presentation?.page?.renderer || "默认结果视图"}
                    </code>
                  </span>
                  <span>
                    对话：
                    <code>
                      {cap.presentation?.conversation?.renderer ||
                        "默认结果视图"}
                    </code>
                  </span>
                  <span>角色：{cap.roles.join(" / ")}</span>
                  {cap.service_id && (
                    <span>
                      远程服务：<code>{cap.service_id}</code>
                      {cap.revision !== undefined &&
                        ` · 连接修订 ${cap.revision}`}
                    </span>
                  )}
                </div>
                <div className="schema-columns">
                  <div>
                    <h4>输入 Schema</h4>
                    <PrettyData data={cap.input_schema} />
                  </div>
                  <div>
                    <h4>输出 Schema</h4>
                    <PrettyData data={cap.output_schema} />
                  </div>
                </div>
              </details>
            ))}
          </div>
        </section>
      ) : (
        <Empty title="尚无模块注册" />
      )}
      <section className="panel contract-template">
        <div>
          <Braces size={23} />
          <h2>以模板开始扩展</h2>
          <p>
            项目提供可复制的模块模板。字段、处理器和呈现声明放在扩展目录，平台保留执行、授权、版本和记录的共同逻辑。
          </p>
          <div className="notice info">
            <Check size={16} />
            安装由本机受控清单管理。模板不会自动执行或默认启用。
          </div>
        </div>
        <pre>
          {
            "templates/module-template/\n├─ manifest.json        能力与呈现声明\n├─ schemas/             输入 / 输出结构\n├─ handlers.py          处理函数\n└─ README.md            接入与验证说明"
          }
        </pre>
      </section>
    </>
  );
}

export default function App() {
  const [session, setCurrentSession] = useState<Session | null>(null);
  const [starting, setStarting] = useState(true);
  const [loginMessage, setLoginMessage] = useState("");
  const [page, setPage] = useState<Page>(initialPage);
  const [registry, setRegistry] = useState<Registry>(emptyRegistry);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const registrySequence = useRef(0);
  const registryPending = useRef(0);
  const registryOwner = useRef<string | null>(null);
  registryOwner.current = session?.user.id || null;
  const [registryError, setRegistryError] = useState<unknown>();
  const navigate = (p: Page) => {
    setPage(p);
    location.hash = p;
  };
  useEffect(() => {
    let active = true;
    void api<Session>("/auth/session")
      .then((value) => {
        if (active) {
          setSession(value);
          setCurrentSession(value);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setStarting(false);
      });
    const expired = () => {
      setSession(null);
      setCurrentSession(null);
      setLoginMessage("登录已过期，请重新登录后继续。");
    };
    const hash = () => setPage(initialPage());
    window.addEventListener("session-expired", expired);
    window.addEventListener("hashchange", hash);
    return () => {
      active = false;
      window.removeEventListener("session-expired", expired);
      window.removeEventListener("hashchange", hash);
    };
  }, []);
  const loadModels = useCallback(async () => {
    try {
      const value = await api<{ items: ModelProfile[] }>("/models");
      setProfiles(value.items || []);
    } catch {
      setProfiles([]);
    }
  }, []);
  const refreshRegistry = useCallback(async () => {
    const request = ++registrySequence.current;
    const owner = registryOwner.current;
    registryPending.current++;
    try {
      const value = await api<Registry>("/registry");
      if (
        request === registrySequence.current &&
        owner === registryOwner.current
      ) {
        setRegistry(value);
        setRegistryError(null);
      }
    } catch (e) {
      if (
        request === registrySequence.current &&
        owner === registryOwner.current
      )
        setRegistryError(e);
    } finally {
      registryPending.current--;
    }
  }, []);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([
      refreshRegistry(),
      api<Record<string, unknown>>("/health"),
      loadModels(),
    ]);
    if (results[1].status === "fulfilled") setHealth(results[1].value);
    setLoading(false);
  }, [loadModels, refreshRegistry]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setInterval(() => {
      if (!registryPending.current) void refreshRegistry();
    }, 5000);
    return () => {
      clearInterval(timer);
      registrySequence.current++;
    };
  }, [session?.user.id, refreshRegistry]);
  useEffect(() => {
    if (session) {
      void load();
      if (
        [
          "models",
          "services",
          "modules",
          "identity",
          "access",
          "devtools",
        ].includes(page) &&
        !isPlatformAdmin(session.user)
      )
        navigate("intro");
    }
  }, [session?.user.id, load]);
  async function logout() {
    try {
      await post("/auth/logout");
      setSession(null);
      setCurrentSession(null);
      registryOwner.current = null;
      registrySequence.current++;
      setRegistry(emptyRegistry);
      setProfiles([]);
      setLoginMessage("");
    } catch (e) {
      setError(e);
    }
  }
  if (starting)
    return (
      <div className="boot">
        <span className="brand-mark">
          <Boxes size={24} />
        </span>
        <Loading label="正在打开共用能力工作台…" />
      </div>
    );
  if (!session)
    return (
      <Login
        message={loginMessage}
        onLogin={(value) => {
          setCurrentSession(value);
          setLoginMessage("");
        }}
      />
    );
  const user = session.user;
  const context: UIContext = {
    user,
    modules: registry.modules,
    capabilities: registry.capabilities,
    onChanged: () => setRefresh((v) => v + 1),
    onModelsChanged: () => void loadModels(),
    onRegistryChanged: () => void load(),
  };
  const pageInfo = pages.find((p) => p.id === page)!;
  return (
    <div className={"app-shell " + (collapsed ? "sidebar-collapsed" : "")}>
      <aside className="sidebar">
        <button
          className="brand brand-button"
          onClick={() => navigate("intro")}
        >
          <span className="brand-mark">
            <Boxes size={22} />
          </span>
          <div>
            <strong>共用能力工作台</strong>
            <small>COMMON WORKSPACE</small>
          </div>
        </button>
        <div className="sidebar-section-label">工作空间</div>
        <nav>
          {pages
            .filter((p) => !p.admin || isPlatformAdmin(user))
            .map((p) => (
              <button
                key={p.id}
                className={page === p.id ? "active" : ""}
                title={p.label}
                onClick={() => navigate(p.id)}
              >
                <p.icon size={19} />
                <span>{p.label}</span>
                {page === p.id && <span className="nav-active-dot" />}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="runtime-status">
            <span className={health?.status === "ok" ? "online" : ""} />
            <div>
              <strong>
                {health?.status === "ok" ? "本机服务已连接" : "本机工作空间"}
              </strong>
              <small>一套能力 · 两种交互</small>
            </div>
          </div>
          <button
            className="sidebar-collapse"
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            {collapsed ? (
              <PanelLeftOpen size={18} />
            ) : (
              <PanelLeftClose size={18} />
            )}
            <span>收起导航</span>
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <ChevronRight size={13} />
            <strong>{pageInfo.label}</strong>
          </div>
          <div className="topbar-right">
            <span className="local-tag">
              <span />
              本机演示
            </span>
            <button
              className="icon-button"
              title="刷新能力与连接"
              aria-label="刷新能力与连接"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
            </button>
            <div className="user-badge">
              <span className="avatar">
                {(user.name || user.username).slice(0, 1)}
              </span>
              <div>
                <strong>{user.name || user.username}</strong>
                <small>
                  {isPlatformAdmin(user)
                    ? "系统管理"
                    : user.role === "member"
                      ? "成员"
                      : user.role}
                </small>
              </div>
            </div>
            <button
              className="icon-button"
              aria-label="退出登录"
              title="退出登录"
              onClick={() => void logout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main className={"main-content page-" + page}>
          <ErrorBoundary resetKey={page}>
            <ErrorNotice
              error={error || registryError}
              onRetry={() => void load()}
            />
            {loading && registry.capabilities.length === 0 ? (
              <Loading label="正在读取能力目录…" />
            ) : page === "intro" ? (
              <Intro
                registry={registry}
                profiles={profiles}
                navigate={navigate}
                user={user}
              />
            ) : page === "devtools" && isPlatformAdmin(user) ? (
              <DevTools user={user} />
            ) : page === "compositions" ? (
              <CompositionWorkbench context={context} />
            ) : page === "module-hub" ? (
              <ModuleHub context={context} />
            ) : page === "business" ? (
              <MyBusiness />
            ) : page === "access" && isPlatformAdmin(user) ? (
              <AccessManagement />
            ) : page === "flows" ? (
              <FlowWorkbench />
            ) : page === "components" ? (
              <InteractionWorkbench />
            ) : page === "workbench" ? (
              <Workbench registry={registry} context={context} />
            ) : page === "chat" ? (
              <Conversations
                registry={registry}
                context={context}
                profiles={profiles}
              />
            ) : page === "records" ? (
              <Records context={context} refresh={refresh} />
            ) : page === "models" && isPlatformAdmin(user) ? (
              <>
                <SectionHeading
                  eyebrow="MODEL CONNECTIONS"
                  title="模型配置"
                  description="保存多个服务连接，选择默认模型，并用真实请求验证连接。"
                />
                <ModelSettings
                  user={user}
                  onChanged={() => void loadModels()}
                />
              </>
            ) : page === "modules" && isPlatformAdmin(user) ? (
              <>
                <SectionHeading
                  eyebrow="MODULE MANAGEMENT"
                  title="模块管理"
                  description="统一查看启用状态与服务心跳，分别管理页面工具和对话能力的发布。"
                />
                <ModuleManagement
                  user={user}
                  onRegistryChanged={() => void refreshRegistry()}
                />
              </>
            ) : page === "services" && isPlatformAdmin(user) ? (
              <>
                <SectionHeading
                  eyebrow="REMOTE SERVICES"
                  title="服务接入"
                  description="连接独立部署的服务，发现能力后发布到固定页面和统一对话。"
                />
                <RemoteServices
                  user={user}
                  onRegistryChanged={() => void load()}
                />
              </>
            ) : page === "identity" && isPlatformAdmin(user) ? (
              <IdentityManagement onChanged={() => void load()} />
            ) : page === "contracts" ? (
              <Contracts registry={registry} />
            ) : (
              <Empty title="当前身份无法访问此页面" />
            )}
          </ErrorBoundary>
        </main>
        <footer className="app-footer">
          <span>共用能力工作台 · 本机功能演示</span>
          <span>确定性处理独立可用，模型能力按配置真实调用</span>
        </footer>
      </div>
    </div>
  );
}
