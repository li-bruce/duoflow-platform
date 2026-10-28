# 共享组件模块开发提示词

服务接入页展示独立监控服务的缓存汇总，健康、负载与正式业务状态分开。组件presence仅为个人界面状态，不承担资源监控；页面轮询不增加底层逐模块采集。汇总过期、监控不可达或指标缺失均显示未知，不以0或业务失败替代。技术报告直接发独立服务，参见 `contracts/monitoring/v1/`。

涉及模块 AI 时，使用 `registration.ai_services` 和同批 AIServiceManager，不把 API Key 塞入组件属性或普通业务表单。主框架的管理员模型连接界面可为每项声明服务保存平台/模块自有来源并测试；两端共用该配置界面及后端权限。模块私有配置、来源绑定、个人编辑草稿与正式业务状态分开。调用前后及最终提交前重新核验当前权限和配置修订，不静默切换供应商；详细规则见 `docs/模块AI服务与配置.md` 与 `contracts/ai/v1/`。

v0.02 开发中的模块套件已加入 module-graph 1.0、flow-exports 1.0、runtime-observation 1.0 和 registration 1.1。原子定义逐项核对实际能力与共享宿主；公开业务流引用明确入口、数据操作、权限和正式完成状态。新远程模块在握手时主动回调平台，平台核对实际收到的挑战。运行事件独立保存，支持去重、补收、缺失提示和当前授权检查；前端实例观察与正式业务状态分开。页面和对话共用原子组件，可用 /business 进入公开业务。套件元数据为 1.1.0，保留旧 1.0.0 包及 registration 1.0 兼容。已有定向契约、模块联调、权限和宿主回归证据；组合执行已实现，完整浏览器验收与新版本发布继续按工作表实施。

平台已实现 composition 1.0：有序业务组合、显式字段连接、独立运行记录、逐步确认、人工办理等待、后台推进和调用凭据恢复。页面与对话引用同一组合运行；整体图关联业务、状态和数据节点。组合角色明确关联各模块角色与各自范围，由管理员整组分配，模块对象检查仍独立执行。协议和用法见 `contracts/compositions/v1/`、`docs/组合业务运行与角色.md`。完整浏览器、独立双 demo 与发布验收仍按维护工作表执行。

组合字段连接逐层检查必填性：必填目标只能读取必定存在的来源，可选目标缺失时省略该字段，明确的 null 仍按类型校验。已开始的组合以已验收声明核对权限与版本；读取或派发前遇到服务暂时离线时保留步骤并等待恢复，授权撤销或声明变化仍停止办理。已发出的写入结果不明时只核对原调用凭据。页面按运行编号隔离异步响应，旧操作返回不得覆盖另一项办理。

请作为模块开发 AI，基于下述冻结接口开发业务组件及后端能力，完成真实宿主接入与测试。固定 Web 与对话使用同一个 React 组件；交互运行时、身份、模型配置、聊天记录和分支由平台负责。本提示词适用于 Codex 或其他能读取文件、运行命令和修改源码的开发工具。

单组件与步骤流程契约版本均为 1.0，属于两个独立接口。基础平台版本：{{PLATFORM_VERSION}}。公开源码契约指纹：{{CONTRACT_DIGEST}}。

本独立平台默认后端为 `http://127.0.0.1:8030`；Vite 开发服务用服务端环境变量 `PLATFORM_BACKEND_URL` 选择后端。两端组件均通过宿主的同源 API 访问本实例，不连接旧项目。

默认首页和 `/#guide` 是公开教学入口，其浏览器练习不是正式组件宿主，不读取账号或业务数据。组件接入与验收仍在需要登录的 `/#flows`、`/#chat`、`/#devtools` 完成。教学练习的本地状态不能替代 WorkflowStepProps、正式业务对象或服务端授权。

`/#developer-guide` 的“框架全貌”讲解主框架、模块共同约定、业务模块、输入输出及交互规则。状态演示读取 framework-lab 声明，区分正式记录、个人步骤和执行结果；讲解不发起正式动作。验收仍进入实际工作台，检查共享组件、后端规则和授权。

## 使用者填写

框架全貌新增“公开与组织”核心分析，明确模块应公开业务行为、数据、接口和共享组件。组合业务通过独立 composition 执行器使用原有共享宿主，页面和对话引用同一运行。不能把浏览器串联示意称为真实跨服务验收。

- 模块需求：【功能目标、输入、实际操作与输出；使用者及数据范围】
- 项目位置：【模块源码目录；平台 common-platform 目录；本次可修改范围】
- 后端方式：【Python 原生 local / Node.js HTTP remote / 其他遵循 HTTP 协议的服务】
- 真实环境：【接口文档、测试数据位置、服务地址；凭据使用本机私有配置，不填入提示词】
- 组件与能力标识：【全局唯一的组件 ID、模块 ID、能力 ID 及版本】
- 验收场景：【页面、对话、恢复、分支、异常；是否测试真实模型】
- 本次边界：【哪些是业务代码，哪些为统一适配层；尚缺哪些运行条件】

## 先核对，再开发

需要即时撤回源内容的组件，应让调用回执仅保存无正文的对象引用，workflow 宿主每次重新读取业务投影。业务服务核验来源、记录、版本、字段分类和投影依赖，覆盖引用、导出、历史回答、共享组件恢复和模型上下文。平台授权快照只验证身份与 grant 变化，不能自动感知源撤回；不可把正文另外存入 summary、workspace、resource、聊天文字或私人草稿后宣称已撤回。引用分支恢复也要验证当前来源。正文快照模式若有需要，必须建设并测试相应宿主依赖协议，当前未提供通用源撤回代理。

1. 阅读本提示词全文及附录。在模块的 `DEVELOPMENT_INPUT.md` 记录使用的契约指纹、提示词文件 SHA-256、需求和环境。记录实际读取行为，不宣称由另一个模型独立开发或评测。
2. 对已有模块，列出可复用业务核心、组件及改造点；新模块按附录骨架创建。缺少平台环境时完成独立实现与契约测试，明确留待宿主联调。
3. 先冻结模块输入输出和合成用例，再写业务组件。组件可以包含筛选、表格、图表及局部步骤；局部编辑直接更新组件状态，不反复调用模型。
4. 本提示词描述实际支持的 1.0 接口。不要通过自造 manifest 字段、URL 脚本加载、独立账号或第二套聊天入口代替接入。

## 交付结构与接入

交付 `component/Component.tsx`、`component/interaction.json`、后端业务能力、测试、启动说明、`PLATFORM_BOUNDARY.md` 和 `DEVELOPMENT_INPUT.md`。模块源码纳入独立本地 Git；验证后以中文规范 commit 提交，不推送，不提交密钥、运行数据或依赖。

前端统一 React 19 / TypeScript。`Component.tsx` 默认导出业务组件，通过 `@platform/interaction-sdk` 导入 `InteractionProps`。组件接收 input、state、result、surface、busy、actions、updateState 和 invokeAction；不得直接调用业务服务 URL、读取平台 Cookie/Key 或维护聊天历史。

安装到 `components/packs/` 的 TypeScript 文件纳入平台类型检查；React、React DOM 及 JSX runtime 统一使用宿主前端依赖。不要在组件包中另装 React 或复制宿主 SDK。独立开发环境可声明这些 peer dependencies；实际接入以平台 `web` 的类型检查和生产构建为准。组件 CSS 与公开源码一起纳入提示词一致性检查。

调用 `updateState` 时提供完整状态对象。宿主会串行保存、显示保存状态，并在提交动作前保存最后修改。网络错误或修订冲突时保留本窗口内容并停止继续保存；“重新读取”会丢弃本窗口未保存编辑。结果以服务返回为准；busy 时禁用业务编辑和执行。动作不可用时禁用相应按钮。

将组件目录复制到平台 `components/packs/<pack>/`，在 `components/installed.json` 的 packs 数组追加目录名，保留已有条目。目录名为小写字母起始，仅字母、数字和连字符。前端构建时发现组件，服务端启动时读取安装清单。安装/升级需要重新构建前端并重启平台；当前没有动态下载远程 JavaScript 的插件商店。

Python local：将后端复制到 `extensions/<module>/` 并追加 extensions 安装清单。handler 接收 ctx 和 payload，返回 CapabilityOutput；ctx.actor 为平台认证身份，ctx.access 提供中央范围判断，ctx.access_stamp 是受控调用的授权快照。注册权限模块先用自有仓储 facts 调用 `ctx.access.check` 再执行状态机；ctx.create_resource 会携带快照。平台统一管理事务，不要自行 commit。

Node.js remote：独立进程，提供 GET `/platform/v1/manifest`、GET `/platform/v1/health`、POST `/platform/v1/invoke`。使用 manifest 1.2 / invoke 1.1，三个端点均验证私有 Bearer 凭据。未注册 access 的旧模块保留固定角色守卫；注册范围权限的模块使用下述 X-Platform-Access 决策桥接，再检查对象和状态，不在失败时退回旧角色允许。管理员发现并启用服务后，组件 actions 引用注册的能力 ID 和版本。组件开发不需要将 Node 代码变成 Python。

Python 同步动作直接返回结果。当前远程协议采用 background：组件动作提交后进入平台队列，宿主轮询运行状态并呈现成功、失败、重试或停止等待。执行期间服务端拒绝编辑；远端停止与本地取消不同，取消不能保证远端副作用未发生。不得把 queued 状态显示为已完成。

两种后端示例具有相同 ID，仅用于二选一。需要同时接入时改成两套唯一 ID，并分别注册组件动作绑定。Node 服务的 npm 依赖独立于平台前端依赖；新增浏览器依赖需显式交付依赖清单，并由平台锁定安装。

## 组件定义规则

interaction.json 遵循附录 Schema。它独立于微服务 manifest；不增加旧协议字段。

- input_schema 与 state_schema 均为对象 Schema，仅使用本地片段引用。initial_state 符合 state_schema。
- id、version、title、description 描述组件。triggers 是可选的完整入口语句；精确匹配无需模型，也可输入 `/component <id>`。
- actions 是具名动作。capability_id 和 capability_version 绑定已注册能力；bindings 将组件 input 或 state 的顶层字段映射到能力输入字段。当前不支持嵌套表达式、任意脚本或浏览器指定目标能力。
- 一个组件可调用多个能力，也可引用不同模块的能力；它们的角色、版本和参数都必须通过平台核验。这不等于已实现任意跨角色 DAG 编排。
- completes 表示该动作成功后当前组件阶段完成。异步任务成功前不可完成。修改状态后此前结果清空，历史能力调用仍保留。
- 每个输入对象和状态对象最多 64 KB；不保存 content_base64 文件字节和已配置凭据。业务大对象以受控引用表示。

## 多步骤工作台：共享步骤与业务对象

有独立业务对象、跨角色办理或长任务时，采用 `@platform/workflow-sdk`，而非把整个网站放入单组件 state。提供 `flow.json` 和 `steps.tsx`；后者导出 `stepComponents`，每个组件接收 `WorkflowStepProps`。固定页面通过步骤导航组织相同组件，对话只渲染当前步骤并显示文字引导。两处共用 React 源码；表单字段、角色决策、校验规则和业务状态名均由模块拥有，不写入通用核心。

安装目录为 `flows/packs/<pack>/`，清单为 `flows/installed.json` 的 packs。使用附录 `contracts/workflows/v1/definition.schema.json`，与旧 interaction 定义、远程 manifest 分开。一个包可有多个 flow；步骤必须具备 id、title、description、component、actions；initial_step 和 target_step 引用真实步骤。每步可声明完整 triggers。`/flow <id>` 打开个人流程，当前对话的 `/step <step_id>` 或精确步骤触发语只切换导航。自由表达通过主平台模型选择打开或切步工具；模型不能据此自动确认、批准、发布或修改业务状态。

固定页面可声明 page_groups，每组有 id、title、steps，引用已注册的步骤；宿主同时渲染该区域的多个组件。对话继续只渲染当前步骤。每个组件保留按 step id 分开的个人草稿；非当前组件的动作先通过正常 PATCH 保存草稿并移动游标，再调用既有 action 接口，不绕过服务端守卫。实例响应 actions/step_actions 是按当前身份和业务状态计算的动作摘要，完整 command_schema 留在静态 catalog。未声明 page_groups 时固定页保持单步导航。 宿主按实例隔离异步读取，拒绝旧响应及修订倒退；重新读取时锁定编辑并等待已发出的保存结束。目录和详情同样丢弃失效请求。页面和对话宿主空闲时约每 15 秒复核可见性；403/404 或流程定义变化清除失权内容，普通网络失败保留私人草稿。轮询是界面更新机制，不能替代服务器每次请求的授权判断。

provider 固定声明 object_type、read_capability，以及需要时的 upload_capability、jobs_capability。前端不提交 provider、URL、身份或 object_ref。读取能力决定谁能打开流程，各动作再分别按能力角色、可用性、版本和业务 projection.allowed_actions 投影；不能要求提交者同时拥有审核权限。流程中的创造对象动作按其能力授权，与当前所选对象能否修改分开。

action 的 id 只用于选择已安装定义，capability_id/version 由服务端固定绑定。默认动作发出 `{object_id, expected_revision, command}`；creates_object 动作只有 `{command}`。command 必须符合声明的 command_schema 和后端能力输入 Schema。可选 `mode: "draft_query"` 仅限 sync query：只发送 `{command}`，不能同时 creates_object，不传正式对象引用，也不改变正式业务状态。它适合显式合成样例比较或模型草案建议；返回值不能冒充正式校验、审批或发布凭证。

必须区分四类状态：模块持有正式业务对象和不可变内容版本；任务执行者持有任务状态与进度；宿主持有每个用户的未提交草稿；个人流程持有当前步骤游标。平台 WorkflowInstance 仅保存 object_ref、drafts、cursor、修订和命令回执引用，不作为业务状态镜像。每次恢复从服务读取当前 projection，并重新计算允许动作。提交和审核必须在模块内按当前角色、对象范围、expected_revision 和状态机再次判断；游标切到某一步不等于取得该步权限。

跨角色交接通过授权业务对象/待办：接手者用自己的流程实例读取同一对象，不复制他人的对话或个人草稿。页面与对话继续同一个个人实例时共享草稿；同对象的其他个人流程使用各自草稿，但共读业务权威状态。分支只复制当前已保存草稿、游标及对象引用，不复制运行中的任务，不回滚业务，不重放任何命令。恢复、切步、创建分支都不是审批事件。定义或授权修订变化后旧个人流程被拒绝，尚无任意版本状态迁移。

SDK 的 updateDraft 提交当前步骤完整草稿，宿主按步骤键保存总计最多 64 KB；execute 先保存草稿，再带个人修订和业务修订执行命令。成功回执与个人执行引用在平台同一事务保存；业务服务仍必须把命令回执与业务修订放在自身同一事务。网络重试保留原请求编号和内容，target_step 成功切换后原请求仍应可安全重读。直接读取不会重放命令；正式动作以实际成功结果为准。

`await execute` 可能只取得后台排队回执，不能视为正式提交完成；宿主 busy 期间的 updateDraft 会被忽略。组件若要在保存后清理输入，应等待当前对象上与本次保存相匹配的正式结果且宿主恢复可操作，再只清理那份已保存草稿，不能清掉其后新输入或用界面位置猜成功。真实宿主测试须覆盖排队、慢回执、失败保留输入和刷新恢复，独立同步测试桩不足以证明这部分行为。

平台 Run 失败后，步骤宿主的“重试原请求”继续同一 Run 和 invocation，使用原始输入；后续修改的个人草稿不会替换该请求。业务 job 的失败与重试仍由模块能力单独实现，不能把重试平台启动请求当作重做整个业务 job。

API 前缀 `/api/v1/flows`：GET `/catalog`、GET/POST `/instances`、GET/PATCH `/instances/{id}`、POST `/instances/{id}/select`、POST `/instances/{id}/actions/{action}`、POST `/instances/{id}/attach`。PATCH 接收 cursor、drafts、expected_revision；select 接收 object_id、expected_revision，空 object_id 只清除当前个人对象引用与草稿，不删除业务对象；action 接收 command、expected_revision、expected_object_revision、idempotency_key。写入均用已有登录会话与 CSRF。

## 文件与长任务适配

文件传输和长任务使用独立 workflow adapter v1，不扩充原 invoke 外壳。小文件 `platform-file` 的单文件 4 MiB 限额保持不变。大文件由上传会话分块传输，正式对象保存 file_ref，不把 base64、文件内容或凭据塞进草稿/消息/模型。浏览器按最多 1 MiB 的片段计算摘要和上传，实际限额由业务存储适配器控制。支持分块不等于已验证任意 GB 容量，必须分别记录小样例与容量试验。

远端固定接口为 POST `/platform/v1/workflow/{operation}`；允许 objects.list/read、uploads.create/status/complete/abort、jobs.status/result/cancel，分块使用独立 PUT 路径。包络、file_ref、upload 与 job 的精确字段在附录。平台仅从管理员现有连接获取 Bearer，注入可信 actor 和绑定 object_ref，执行前后检查授权和连接修订；模块再次检查机构、所有者、对象、upload/job/file 的真实关联。知道编号不等于可访问文件。

大结果必须在模块内持久保存，objects.read、业务命令、上传和 job 结果只返回有界摘要，不重复内联全部明细。objects.list 接收 object_type、可选 cursor 和 limit（1—50），返回 objects 与 next_cursor；只可请求 provider.object_type 或显式 related_object_types（最多5类）。objects.detail 接收绑定 object_ref、模块声明的 section、cursor、limit，返回 object_ref、revision、items、next_cursor、total。详情章节由模块白名单裁决；游标不是授权，逐次核验身份、对象及修订。超长单值的裁剪必须明示，不能伪装完整内容。列表响应上限128 KiB、对象投影1 MiB、详情512 KiB，最终合并的个人 Flow 响应仍限2 MiB。不得通过提高全局上限来容纳不断增长的报告或版本历史。

宿主 SDK 的 detailPage(section,cursor?,limit?) 和 loadMoreObjects(objectType) 使用同源 POST `/instances/{id}/detail`、`/instances/{id}/objects/list`。objectPages 提供各授权类型的目录游标。分页内容只保存在当前视图内存，不进入个人草稿或模型上下文；切换对象、业务修订或章节后废弃旧页和未完成响应。恢复按当前状态重新读取，不用旧页替代业务记录。

平台同源 API 位于 `/api/v1/flows/instances/{id}`：POST `/uploads`，GET `/uploads/{upload_id}`，PUT `/uploads/{upload_id}/chunks/{index}`，POST `/uploads/{upload_id}/complete|abort`，GET `/jobs/{job_id}`、GET `/jobs/{job_id}/result`、POST `/jobs/{job_id}/cancel`。create 上传必须有稳定 idempotency_key；complete、abort、cancel 带 expected_revision。断点恢复需重新选择同一个文件，校验长度和 SHA-256；服务验证分块和完整摘要后原子关联，明确失败清理、孤立文件和恢复策略。

任务 start 由已登记业务能力实现，并返回 job_ref；平台调用成功只表示启动命令完成，任务结果必须从业务 job 状态取得。取消只有执行者确认尚未提交且停止后才是 cancelled，提交点后应拒绝取消；HTTP 超时或停止本地等待不证明远端停止。声明并验证进度、失败、手动重试、重启恢复和不重放。适配器不自带调度器、跨服务事务、Temporal 或任意副作用“恰好一次”保证。

Python 也可以作为正式业务对象的所有者。受控 local 模块在 manifest 顶层声明 `workflow_adapter: "adapter:create"`；工厂只能引用包内简单文件名与函数名。factory(context) 接收不可变 LocalWorkflowContext，其字段为 sessions、模块专属 data_dir、module_id；不包含请求身份、runtime 或模型网关。返回对象实现 `request(*,actor,capability_id,operation,payload,request_id)`，可选 `upload_chunk` 的精确参数见模板/传输接口。平台逐次注入已授权 actor，adapter 返回与 HTTP data 相同形状；handler 负责业务写入，adapter 提供同源的授权 read/list、文件或任务操作。Python 示例中某个能力只比较样例，是模块选择，不是框架对 Python 的限制。未实现的文件/任务方法返回 501；框架没有自动替每个 Python 模块提供大文件存储。

需要模型提炼规则时，先生成可读且受限的 DSL 草案，严格验证操作白名单和参数，再用合成样例试跑与差异对照，由独立人工审批发布不可变规则版本。运行时执行确定性规则，不执行模型生成的 Python/JavaScript、任意表达式或正则。模型失败、无效结构和超时必须保留为真实失败；“模型已建议”不代表“规则已发布”。 本地 ctx.gateway.chat 可返回白名单 finish_reason（stop、length、max_tokens、tool_calls、content_filter、function_call），仅在供应商实际返回受支持值时保留；缺失不能推断完成。模块可在既有安全错误详情中分类空内容、截断、JSON 或 DSL 不合格，不能写入原始模型回复和凭据；提示中的输出根结构须与校验 Schema 一致。

## 身份、恢复与分支

开发多职责业务时，先给出角色职责、动作权限、对象范围、状态条件、字段投影和 Web/对话步骤矩阵。共享正式对象和业务状态机；提交、处理、审核等职责可以组织不同办理路径，共用证据或编辑组件，专有任务使用专有组件。组件的隐藏、只读和禁用只是呈现，真实数据投影与动作授权必须在服务端执行。列表、数量、附件、模型上下文和历史结果同样需要权限判断。

当前已支持独立 `contracts/access/v1` 权限包：local 模块交付 `access-definition.json` 由受控安装加载；remote 在发现后通过主框架管理入口注册相同附件。远程顺序为发现并保持禁用、安装需要的 flow/组件、注册附件、启用、再授权；启用但尚无 grant 的新模块拒绝访问。包中声明 permissions、role_templates、scope_keys、command_mappings、experiences、workitem_sources 等严格字段，完整格式见附录。不向现有 manifest、flow 或 actor 私加角色和权限。权限、角色模板、办理视图和待办源都使用本模块命名空间。安装只注册定义，不授予用户权限。实际授权由有 identity.manage 的管理者按单个用户和完整 scope 分配；尚无组授权、委托管理员或外部 OIDC 登录。

同一授权内的项目/机构/owner 等条件同时满足，不同授权之间不能拼接条件。旧授权保留原权限快照，升级新增权限须重新授予；收紧和撤销生效。actor_is_fact、actor_not_fact 和 fact_in 守卫使用模块仓库事实，缺失默认拒绝。Casbin 模型由框架固定，模块不提供任意执行表达式。使用 ctx.access.check 作实际对象授权；has_permission 只供目录，matching_scopes 返回完整 OR-of-AND 范围。列表、数量、字段和下载先授权再输出。模块仍负责业务状态机、并发修订和幂等。

experiences 按 required_permissions 映射已安装 flow_id，可选 step_ids 限定可见步骤，宿主同时过滤 Web page_groups 和对话步骤。多个获准 experience 的步骤可合并；这不增加动作权限。已注册模块没有匹配 experience 即拒绝打开，不能退回旧角色。不同职责可组织独立 flow 并复用组件；同一人跨项目兼任职责时，给不同路径绑定各自 read capability，让服务器端目录和单对象读取落实职责范围。前端隐藏和只读仅为呈现。

统一“我的业务”API：GET `/api/v1/business/catalog` 查询办理入口，GET `/api/v1/business/instances` 返回本人最近最多 100 个可恢复流程，GET `/api/v1/business/objects?flow_id=...` 经授权 provider 实时读取目录，POST `/api/v1/business/open` 以 instance_id 恢复本人流程，或以 flow_id、可选 object_id、idempotency_key 建立工作台；surface 可选 page/conversation。要延续私人草稿，应传原 instance_id；仅有同一业务 object_id 不代表使用同一个私人实例。workitem_sources 是注册元数据，目前中心按 flow provider 聚合，不自动执行任意来源能力，没有另建正式业务状态或通用待办调度。

已注册模块的个人实例和调用记录、平台资源、生成回复会校验 access_stamp。本人授权、到期或定义变化后旧快照拒绝使用；其他人的单独授权变化不使本人失效。当前保守绑定整个有效授权集合，可能同时使本人其他 scoped 私人实例失效，尚无按项目的旧草稿自动迁移。重新办理读取模块正式对象；跨人交接不复制私有对话和草稿。未注册 access 的旧模块仍走原有固定角色路径，不代表它们已完成新权限迁移。

Node 与其他远程后端通过 `X-Platform-Access` 短时票据向配置固定地址的 POST `/api/v1/access/remote-decisions` 请求判断，正文为 request_id、operation、permission、facts；票据放同名请求头。先验证原服务 Bearer、从自有数据库读 facts，再请求决策；operation 与本次 invoke/workflow 操作一致。返回 allowed、reason、policy_revision，失效、错误或超时默认拒绝。票据不进入前端、模型、日志、业务持久化，也不是 OAuth 登录或长任务委派。附录 node.mjs 是服务端客户端；默认签名密钥仅在主框架单进程内存，未提供多副本密钥治理。业务提交仍要检查状态、修订、幂等和提交前权限。

跨模块本地交接可通过受控仓储接口，在同一事务内建立独立下游对象，保存唯一来源键及不可变来源版本。拆到不同服务器时需另外落地 outbox/inbox、重复投递去重与最终状态查询；共享组件、授权桥接和中心入口不会自动提供分布式事务或任意 DAG 编排。

实例属于当前登录用户，并记录授权修订。恢复时重新核验身份、组件定义和绑定能力。改变权限后旧实例不继续暴露状态。固定页和对话明确使用同一实例时共享草稿；新建实例相互隔离。

页面的“在对话中继续”将实例关联到本人对话。对话分支复制分支范围内组件的当下已保存状态到新实例，不重放业务动作。它不提供任意历史时刻的草稿回放。正在运行的任务不复制执行，新分支保留输入进入编辑状态；已完成的结果可作为分支参考。

组件定义和版本一旦变化，旧实例暂不可编辑，宿主提示重新创建；当前尚未实现状态迁移。微服务停用或能力无权时组件不可用；离线时保留经过授权的既有状态，阻止新动作。

## 宿主 API 与现有智能能力

新业务优先使用同批 `模块开发快速提示词.md` 和 `templates/module-kit/`。简单工具提供输入输出和一个交互组件；流程业务额外声明正式对象、职责与状态步骤。共享组件保持只使用宿主 SDK，模块服务端使用模板配套的权限接线；并发、幂等和专业规则仍需业务实现。生成包静态检查不能替代安装后的两端真实浏览器验收。

### 身份与入口可见性边界

逐项说明组件入口可发现、可进入、数据可读取、动作可执行的条件。两端使用同一后台授权和字段投影；登录、打开组件或取得模型建议均不增加业务权限。新 system_admin 不自动获得业务材料，模块参与者也不自动获得平台管理或对别人授权的权限。当前中心 identity.manage 可管理实际 grant，受范围限制的委派管理尚未实现。

当前宿主要求登录，没有游客会话、自助注册或通用公开入口策略；不要给现有 manifest/flow/actor 添加自造 public、guest、audience 字段。未来若开放游客组件，应由框架提供统一匿名策略及互相隔离的临时会话，不能共用游客账号。登录后的会话升级应重新授权，不自动提交。公开与内部数据分别投影，历史、附件和办理消息也需检查当前权限；完整参与者消息与持久通知尚未实现。

独立模块 Web 直接地址同样需要后端鉴权；共享组件依赖宿主会话，旧 embedded iframe 不传递平台登录凭据，不能视为 SSO。页面公开下载的代码壳不应内含受保护数据；隐藏菜单、前端只读或模型指令都不是权限边界。公开访问需求作为设计附件交付，在主框架协议正式升级前保留为待接入项。

UI 组件通过 SDK 使用以下接口，不自行重复包装认证。API 前缀 `/api/v1/interactions`，写请求使用已有会话的 CSRF 令牌。

| 方法与路径 | 主要字段/用途 |
| --- | --- |
| GET /catalog | 当前身份可用的组件定义及可用状态 |
| POST /instances | component_id、input、idempotency_key、可选 conversation_id |
| GET /instances | 本人最近 100 个可访问实例 |
| GET /instances/{id} | 当前状态、修订、结果、动作与任务状态 |
| PATCH /instances/{id} | state、expected_revision；冲突返回 409 |
| POST /instances/{id}/actions/{action} | expected_revision、idempotency_key；参数由服务端绑定 |
| POST /instances/{id}/attach | 创建或继续本人关联对话；保留同一实例 |

自然语言仍使用主框架模型配置。模型可选择 core.interaction.open 并提出初始参数，平台校验后创建 UI；业务动作由用户操作组件触发。CopilotKit 1.73.3 的公开 v2/context 与 v2/headless 接口负责对话中注册和渲染：每个宿主实例持有本地呈现注册表，不连接 CopilotKit Runtime。不要替换成缺少真实连接的完整 CopilotKitProvider，后者在生产模式会拒绝初始化。平台未切换到 CopilotKit 的 Agent 后端，也没有新增 AG-UI 执行接口。

## 验收与交付

先验证纯业务函数，再验证 Schema、角色与对象范围，再用真实宿主测试。至少覆盖：固定页面和对话同组件、显式入口、模型工具选择（如适用）、实际服务结果、刷新与重新登录、同组件多实例、分支隔离、并发修订冲突、重复提交、服务离线、任务失败与恢复、定义版本不一致。

“渲染卡片成功”不能替代动作、状态和真实后端验证。无模型入口、替代模型和真实模型测试分别记录。合成数据不声称是真实业务数据；生产级专业效果需领域评测。

对话呈现需要在生产构建后的真实浏览器里测试；开发模式或 jsdom 通过不代表生产初始化正确。验收应覆盖进入已有对话、操作组件、后台结果更新和刷新恢复，并检查页面异常与意外的外部 Runtime 请求。

完成时交付简单操作步骤、架构与数据流、接线边界、测试命令和结果、尚未实现部分。提出统一协议变化时先报告给框架任务，不自行修改认证、通用运行时或其他模块。需要安装自己的组件包、注册自己的能力及补充宿主验收测试时，在明确授权范围内完成并记录。

{{COMPONENT_APPENDIX}}


## 通用架构、状态机与开发调试台

业务事实以模块返回的 projection.state 为准，个人流程 cursor 和任务 Run.status 分开处理。workflow 可以声明 state_machine；完整 Schema 随契约附录导出。它描述业务状态、转移及 step_id/action_id 引用，校验器检查重复、起点、端点与动作关系。guard_description 仅供说明，权限和状态守卫仍由模块后端执行；不得以浏览器改状态或模型文字代替业务事务。无状态机的历史流程显示未声明，不用导航步骤冒充状态。

模块开发完成后使用 /#devtools 检查 capability / interaction / workflow 的定义、引用版本、前端安装情况与可用性。JSON 校验不安装、不执行源码、不访问网络引用；在线交互 state_schema 正则不执行，含该类规则时到离线受控校验。调试台仅平台管理员可用，且只观察本人流程和当前有权业务对象；每2秒轮询、暂停/手动刷新、图与正式 FlowHost 并排。观察历史最多100条，仅当前对象的状态元数据，不含正文与草稿，也不是完整业务事件审计。源码已安装与浏览器构建已加载分别核实。

浏览器 presence 心跳只核对当前身份、本人实例、中央授权指纹、组件定义和个人修订，不读取远程业务对象或列表，不采用 Run 回执、不推进个人步骤。主动调试读取继续重新授权并获取正式投影；presence 新鲜不能作为业务状态或源权限有效的证据。

组合运行图的入口 activity 来自组合步骤状态，入口完成应标为步骤已完成；原子节点的调用状态来自实际调用/观察证据，两者不能共用“调用成功”的含义。步骤可以通过读取已完成业务对象而完成，图中仍须独立显示缺少调用记录，不能用蓝色入口补造运行见证。

优先用 framework-lab 中立模块演练：创建→提交→退回→修改→重提→完成，页面/对话共用步骤组件和对象。它是本人调试样例，不是允许自审的真实审批规则。Node workflow-node 模板也从共享flow状态声明执行转移；其他模块可用自有业务状态引擎，仍返回统一投影。

架构与迁移入口见 docs/通用框架架构与代码地图.md、docs/模块开发与调试说明书.md、docs/框架迁移与AI交接指南.md。scripts/export_framework.py 从正向源码清单导出全新通用包，仅安装 framework-lab，重建提示词与说明书，不复制业务数据/凭据/远程连接/行业演示账号/依赖/构建物/Git历史。新包仍需安装锁定依赖、构建并实际验收；导出摘要不是运行验收。
# 模块接入验收同步

远程能力宿主使用 registration 1.0 双向验收。组件和流程本身仍由受控安装与生产构建加载；模块应在 host_requirements 声明实际需要的编号和版本，公开对象接口与真实分派表一致。主框架逐项校验声明、身份拒绝、健康、无效请求拒绝、模块主动确认和平台安装依赖，全部通过才能发布。权限附件先注册，实际用户授权另外管理。Schema 与两侧架构见同批 contracts/registration/v1、docs/主框架说明.md 和 docs/模块框架说明.md；业务组合、通用字段授权和业务正确性不由接入报告证明。
# 跨模块来源读取补充

共享组件只选择稳定来源引用与版本，不收集或保存源授权票据。服务端可以使用 `source-reads-1.0`，由平台在当前目标票据下重新检查目标和已登记源对象的权限。源读取失败、版本变化或撤权时保留明确失败，界面位置与聊天分支不替代正式来源授权。准确协议见随包 `contracts/access/v1/source-reads.md`。

## 业务协同与两种宿主

独立业务协同中心通过模块事件协调并行业务，主框架管理订阅、公开审批和有界汇总。正式对象状态、个人步骤、调用回执、技术健康与中心消息状态分别表达各自事实。页面与对话继续使用同一模块组件和对象adapter；办理动作由模块保存状态并同事务写outbox，组件随后读取正式projection。中心摘要用于显示相关变化，数据正文继续按模块当前授权读取。

模块的可选coordination声明和Node/Python SDK见 `contracts/coordination/v1/` 与 `templates/module-kit/sdk/`。公开审批回执送回来源模块，处理函数核对申请编号和修订后执行公开；接收消息的模块在同事务保存自身处理和inbox，再确认投递。框架管理页入口为 `/#coordination`，模块业务可在页面或对话继续办理。
