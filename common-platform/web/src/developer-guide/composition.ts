export const SUPPORT = [
  { id:'services', title:'管理微服务', level:'已有基础', current:'登记连接、发现功能、双向核对接口；七项接入检查全部通过后发布，持续检测健康。', limit:'这是管理服务接入；不会替部署环境启动、扩容或升级远端进程。', next:'把接入服务、对外功能和组合步骤关联起来，清楚显示一个服务变化会影响哪些业务。', sources:['app/remote_services.py','app/service_monitor.py','app/module_state.py'] },
  { id:'identity', title:'统一认证与权限', level:'已有基础', current:'使用平台登录身份，向模块传递可信用户；模块可以按机构、项目等范围判断权限。', limit:'同一个账号进入多个模块，不等于自动拥有所有数据。外部统一登录和长任务授权交接还需另行实现。', next:'每一步重新核对目标模块和记录的权限，组合本身不能给用户增加权利。', sources:['app/security.py','app/access_control.py','app/access_bridge.py'] },
  { id:'actions', title:'公开业务行为', level:'已有基础', current:'模块登记功能及输入输出，并公开数据、业务步骤和权限要求；平台与模块双向确认后才允许发布。', limit:'格式清楚，不等于业务含义完整。哪些状态允许操作、会影响哪些记录、失败后能否重做，尚未统一描述。', next:'补充前置条件、影响对象、结果含义、重复请求和失败查询方式；后端仍执行真实判断。', sources:['app/registry.py','app/module_contract.py','app/contracts.py'] },
  { id:'data', title:'公开业务数据', level:'部分支持', current:'可以返回结果数据、资源、记录目录、单条记录和详情，并给出记录类型、编号、版本、状态及可用动作。', limit:'已能登记公开数据类型与操作；跨模块数据关系和通用读取目录尚未实现；一个多步骤流程只有一个主记录接口和当前主记录。', next:'声明数据类型、字段含义、读取方式、关联引用、版本与范围。公开有权读取的数据接口，不直接共享数据库。', sources:['app/workflows.py','app/workflow_transport.py','contracts/workflows/v1/definition.schema.json'] },
  { id:'interfaces', title:'统一接口对接', level:'已有基础', current:'本地函数或受管理的 HTTP 请求都进入同一调用入口；独立服务公开功能说明、健康和调用接口。', limit:'不能直接把任意业务网址变成已接入功能。已有系统需要按共同格式接收身份、输入并返回结果。', next:'保留现行接口，用服务端适配连接已有系统；业务组合只引用已登记功能。', sources:['app/runtime.py','app/remote_services.py','docs/remote-service-protocol.md'] },
  { id:'handoff', title:'把结果交给下一模块', level:'部分支持', current:'结果可以提供后续操作，指定另一个已登记功能和明确的输入；界面预填表单，提交后执行。隔离测试已验证两个模块之间可这样连接。', limit:'不会自动执行，也不会自动理解同名字段或转换数据类型。目标权限仍单独检查。', next:'增加明确的数据连接：哪一步的哪个输出，对应下一步哪个输入；连接前检查格式、版本和授权。', sources:['app/runtime.py','web/src/WorkspaceView.tsx','app/conversation.py','tests/test_composition_boundaries.py'] },
  { id:'organization', title:'自由组织跨模块业务', level:'需要新增', current:'已有组件和步骤定义，可按明确功能编号调用；具备构建组合业务的基础零件。', limit:'当前对话流程声明的步骤限定在同一模块；多步骤格式没有多主记录接口或跨步骤数据连接字段，也没有独立的组合运行记录。', next:'增加属于总框架的组合定义、校验器与执行记录，统一管理步骤、数据连接、各模块记录和完成条件。', sources:['app/workspace_contract.py','app/agent_engine.py','app/workflows.py','contracts/workflows/v1/definition.schema.json'] },
  { id:'surfaces', title:'组合业务的页面与对话', level:'部分支持', current:'模块组件可以在页面和对话共用，正式记录与个人查看步骤分别保存。', limit:'这证明单模块的两种呈现基础；完整的跨模块组合定义和组合运行界面尚未实现。', next:'页面组合多个步骤，对话显示当前步骤；两者读取同一组合编号、同一进展和同一批模块结果。', sources:['web/src/workflows/FlowHost.tsx','web/src/workflows/ConversationWorkflow.tsx','web/src/workflows/sdk.ts'] },
  { id:'recovery', title:'失败后继续与恢复', level:'部分支持', current:'单次调用有请求编号、回执、版本核对和后台执行记录；个人会话可以恢复。', limit:'没有整个组合的步骤记录、通用反向操作或跨服务回滚。远程超时不能证明业务没有发生。', next:'逐步保存输入引用和回执；失败时停止后续，保留已完成结果。只有模块明确提供的反向操作才能撤销业务。', sources:['app/runtime.py','app/remote_services.py','app/db.py'] },
];
export const EXPOSURES = [
  { title:'业务行为', text:'说明能做什么、输入和结果是什么、在哪些情况下允许做、会改变什么。', example:'提交资料：接收资料编号与版本；检查归属和状态；返回新版本与处理结果。' },
  { title:'业务数据', text:'说明哪些记录可以读取、字段含义、记录之间怎样关联，以及如何按权限读取。', example:'资料编号、当前版本、来源、内容摘要；下一模块通过明确引用取得所需内容。' },
  { title:'接口与界面', text:'提供统一调用与读取接口，并提供页面和对话可共用的操作组件。', example:'服务连接由总框架管理；模块不让浏览器自行拼接服务地址或取得连接凭据。' },
];
export const DESIGN = [
  { title:'模块公开说明', owner:'模块负责', text:'沿用已有功能和接口格式，补充业务含义、数据类型、影响和失败处理说明。模块保留自己的正式记录与规则。', outcome:'总框架知道每个模块能做什么，也知道怎样正确连接。' },
  { title:'组合定义与检查', owner:'总框架负责', text:'用独立组合定义选择功能、顺序、数据来源、确认步骤和完成条件。先检查目标存在、版本匹配、必填内容齐全和数据格式相容。', outcome:'自由组织的是已公开且可正确连接的功能，不靠相似字段名称猜测。' },
  { title:'组合执行与记录', owner:'总框架安排，模块执行', text:'每次办理产生一个组合编号，逐步保存记录引用、版本、请求编号和回执。每一步重新检查权限；成功才向后传递，失败保留之前的结果。', outcome:'A 完成而 B 失败时，能看清停在哪里，继续时不会重复执行 A。' },
  { title:'同一业务，两种呈现', owner:'页面与对话共用', text:'页面同时呈现多个步骤和结果，对话聚焦当前步骤。它们使用同一组合定义、同一运行编号和相同模块组件。', outcome:'在页面完成登记，可以到对话继续处理；切换方式不会新建另一条业务。' },
];
export const COMPOSITION_SOURCES = ['app/registry.py','app/runtime.py','app/workflows.py','app/workspace_contract.py','web/src/WorkspaceView.tsx','tests/test_composition_boundaries.py','docs/模块公开与业务组织分析.md'];
