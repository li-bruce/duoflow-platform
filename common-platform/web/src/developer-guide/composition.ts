export const SUPPORT = [
  {
    id: "services",
    title: "管理微服务",
    level: "已实现",
    current:
      "登记连接、发现功能、双向核对接口；全部接入检查通过后发布，并持续检测健康。1.1 握手主动验证运行状态回传。",
    limit: "远端进程的部署、扩容和升级由实际运行环境负责。",
    next: "服务连接或公开声明改变后重新验收，已保存组合检查原版本。",
    sources: [
      "app/remote_services.py",
      "app/service_monitor.py",
      "app/module_catalog.py",
    ],
  },
  {
    id: "identity",
    title: "统一认证与权限",
    level: "已实现",
    current:
      "同一平台身份可进入多个模块。组合角色明确关联各模块角色及机构、项目范围，由管理员整组分配。",
    limit:
      "模块仍依据实际对象检查权限；外部统一登录与长任务授权交接需要另行实现。",
    next: "每步重新核对来源和目标权限，撤销授权会停止后续调用。",
    sources: [
      "app/access_control.py",
      "app/access_bridge.py",
      "app/compositions.py",
    ],
  },
  {
    id: "actions",
    title: "公开业务行为",
    level: "已实现",
    current:
      "模块登记前后端原子、输入输出、事件、状态和业务节点，再公开完整流程、部分流程或单项功能。",
    limit: "公开图描述约定，正式状态和业务判断由模块执行。",
    next: "部分流程使用具名入口及明确完成状态，单项操作使用独立功能编号。",
    sources: [
      "app/module_graph.py",
      "app/flow_exports.py",
      "app/registration_contract.py",
    ],
  },
  {
    id: "data",
    title: "公开业务数据",
    level: "已实现",
    current:
      "模块公开对象类型、读取接口、数据操作和权限。平台连接明确字段，传递前重新读取有权访问的对象。",
    limit: "一个共享流程围绕一个主对象；任意数据转换须由模块提供明确功能。",
    next: "必填字段连接必定存在的来源；可选输入可以留空，来源变化时重新检查。",
    sources: [
      "app/composition_contract.py",
      "app/compositions.py",
      "app/workflow_transport.py",
    ],
  },
  {
    id: "interfaces",
    title: "统一接口对接",
    level: "已实现",
    current:
      "本地函数和已接入的远程接口进入相同调用入口，保持身份、输入输出和回执格式一致。",
    limit: "已有服务需按公开协议适配，组件与流程由平台受控安装。",
    next: "平台保存管理员配置的连接，浏览器通过平台调用已登记功能。",
    sources: [
      "app/runtime.py",
      "app/remote_services.py",
      "docs/remote-service-protocol.md",
    ],
  },
  {
    id: "handoff",
    title: "把结果交给下一模块",
    level: "已实现",
    current:
      "指定前一步的输出字段与下一步输入，保存前检查类型和必填条件。前一步完成后自动继续，需要确认时等待用户操作。",
    limit: "同名字段不会自动关联。复杂转换由单独模块功能完成。",
    next: "对象传递时复核当前内容和权限，来源变更后暂停后续步骤。",
    sources: [
      "app/composition_contract.py",
      "app/compositions.py",
      "app/composition_execution.py",
    ],
  },
  {
    id: "organization",
    title: "自由组织跨模块业务",
    level: "已实现",
    current:
      "在组合业务界面选择模块公开入口，保存顺序、数据连接、角色和独立运行记录，每个组合最多 20 步。",
    limit: "当前支持顺序组合；条件分支、循环、并行与自动补偿仍待实现。",
    next: "每个保存版本保持原内容；模块或连接变化后重新检查。",
    sources: [
      "contracts/compositions/v1/README.md",
      "app/compositions.py",
      "web/src/compositions/CompositionEditor.tsx",
    ],
  },
  {
    id: "surfaces",
    title: "组合业务的页面与对话",
    level: "已实现",
    current:
      "页面与对话引用同一组合运行，使用相同模块组件，正式对象和个人草稿分别保存。",
    limit: "对话指令用于选择入口和准备输入；真实语言模型本轮尚未验收。",
    next: "刷新和对话分支保留原运行引用，新的办理需要明确开始。",
    sources: [
      "web/src/compositions/CompositionRunView.tsx",
      "app/conversation.py",
      "app/compositions.py",
    ],
  },
  {
    id: "recovery",
    title: "失败后继续与恢复",
    level: "已实现",
    current:
      "保存每步输入、调用标识和回执。暂时离线时等待恢复，进程中断后可以核对原调用结果。",
    limit:
      "跨服务效果由各模块保存；结果不明的写入保持待核对，自动补偿尚未实现。",
    next: "授权或版本改变后重新检查；暂时离线保留原步骤，已完成的效果不会重复执行。",
    sources: [
      "app/composition_execution.py",
      "app/composition_models.py",
      "tests/test_composition_workflow.py",
    ],
  },
];
export const EXPOSURES = [
  {
    title: "业务行为",
    text: "说明能做什么、输入和结果是什么、在哪些情况下允许做、会改变什么。",
    example:
      "提交资料：接收资料编号与版本；检查归属和状态；返回新版本与处理结果。",
  },
  {
    title: "业务数据",
    text: "说明哪些记录可以读取、字段含义、记录之间怎样关联，以及如何按权限读取。",
    example:
      "资料编号、当前版本、来源、内容摘要；下一模块通过明确引用取得所需内容。",
  },
  {
    title: "接口与界面",
    text: "提供统一调用与读取接口，并提供页面和对话可共用的操作组件。",
    example:
      "服务连接由总框架管理；模块不让浏览器自行拼接服务地址或取得连接凭据。",
  },
];
export const DESIGN = [
  {
    title: "模块公开说明",
    owner: "模块负责",
    text: "沿用已有功能和接口格式，补充业务含义、数据类型、影响和失败处理说明。模块保留自己的正式记录与规则。",
    outcome: "总框架知道每个模块能做什么，也知道怎样正确连接。",
  },
  {
    title: "组合定义与检查",
    owner: "总框架负责",
    text: "用独立组合定义选择功能、顺序、数据来源、确认步骤和完成条件。先检查目标存在、版本匹配、必填内容齐全和数据格式相容。",
    outcome: "连接关系明确对应公开功能与字段，每一步都有可核对的输入输出。",
  },
  {
    title: "组合执行与记录",
    owner: "总框架安排，模块执行",
    text: "每次办理产生一个组合编号，逐步保存记录引用、版本、请求编号和回执。每一步重新检查权限；成功才向后传递，失败保留之前的结果。",
    outcome: "A 完成而 B 失败时，能看清停在哪里，继续时不会重复执行 A。",
  },
  {
    title: "同一业务，两种呈现",
    owner: "页面与对话共用",
    text: "页面同时呈现多个步骤和结果，对话聚焦当前步骤。它们使用同一组合定义、同一运行编号和相同模块组件。",
    outcome: "在页面完成登记，可以到对话继续处理；切换方式不会新建另一条业务。",
  },
];
export const COMPOSITION_SOURCES = [
  "app/module_graph.py",
  "app/flow_exports.py",
  "app/module_catalog.py",
  "app/compositions.py",
  "app/composition_execution.py",
  "app/composition_graph.py",
  "docs/组合业务运行与角色.md",
];
