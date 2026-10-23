# v0.4 微服务集成契约

现行补充（2026-10-22）：远程模块发布须通过 [registration 1.0 接入标准](../contracts/registration/v1/README.md)。它单独管理注册声明与双向确认，不改变下述 invoke 包络。发现之后还须注册权限、安装所需组件/流程、完成七项验收；旧连接没有验收记录时不自动发布。完整双方说明见 [主框架说明](主框架说明.md) 和 [模块框架说明](模块框架说明.md)。

状态：2026-09-22。新协议对应框架 v0.4；测试证据与部署状态见集成报告。

## 冻结的接线规则

机器可读 Schema 在 `contracts/v1.2/`；服务应复制当前文件到自身受控适配目录，并记录版本。`app/workspace_contract.py` 是框架维护源，日常业务改动不修改它。

1. 新 manifest 顶层 `protocol_version: "1.2"`；其余模块/能力声明沿用完整旧模板。调用请求和响应使用 `protocol_version: "1.1"`，精确结构为 invoke-request-1.1.schema.json 与 invoke-response-1.1.schema.json。
2. context 仍只有 workspace_id 与 actor。actor 字段完整为 id、role、roles、organization_id（可空）、team_ids、name、authorization_revision。roles 包含有效角色（业务角色可能含基础 member），不能只按主 role 判断多角色用户。主 role 必须属于 roles。框架重新授权后生成上下文；服务端 Bearer 鉴权成功后再使用。
3. workspace_id 是共同部署的业务空间，默认 `local`，不是用户私有空间。组织与团队是这个空间中的归属范围。contributor 只在获授权本机构办理；researcher 按本人及明确团队共享访问。curator/reviewer 可按其业务角色处理当前 workspace 的跨来源共建单位任务；来源机构字段不等于登录组织。system_admin 不默认拥有上述业务能力。
4. `presentation.page.renderer` 与 `.conversation.renderer` 可用 `workspace`。返回 data 必须含 `blocks`，其余 data 业务字段可保留。blocks 的严格类型为 text/table/metrics/chart/map/evidence/actions/image/download，结构见 workspace.schema.json。禁止任意HTML、URL执行、JS和iframe片段。
5. actions.items 使用 `{label, capability_id, input}`，input 是下一步预填数据；平台按当前能力权限渲染表单，用户提交才执行。对象ID/版本由服务真实结果填入，不由模型伪造。动作不是远端已执行命令。
6. 文件参数使用对象 Schema `format: "platform-file"`，值为 `{filename, media_type, content_base64}`（不是data URL），单文件 4 MiB，整个 invoke JSON 最多 12 MiB。可作为顶层 property，也可作为 array.items。服务仍需核验真文件类型、路径/图片和表格规则。大包与离线图片不伪装成这个有限接口已支持，提供清单及受控接入适配。新协议响应最多 8 MiB；下载/图片 blocks 单件也限 4 MiB，受业务权限约束。
7. `surfaces.conversation` 在原 capability_ids/starter_prompts 外增加可选 `skills` 数组。单项字段为 id/title/description/triggers/entry_capability/steps，精确结构见 skill.schema.json；每步为 id/title/capability_id。第一步引用入口能力，引用的能力必须在模块中存在；最大30个skill/每个20步。一个能力可聚合有关系的动作，但每个动作在服务端仍独立鉴权和校验。
8. 进入流程的确定指令为 `/skill <module_id>/<skill_id>`，中文 triggers 是精确或明确的入口短语；一般自然语言由模型或规则路由辅助。业务服务提供流程的引导与下一步 actions，框架负责会话、状态、分支、身份和执行记录。模型不能绕过表单提交、权限或业务审核。
9. 固定web入口采用平台原生能力表单及workspace组件；同样组件在对话中使用。原私有页面的外站SSO不在这份契约中，服务不得自行实现通行码或暴露无密码角色切换以代替统一认证。

远程调用本身仍为一次HTTP请求内完成，最大等待300秒；不新增任意远端轮询/回调/202长任务。多个业务步骤通过持久对象编号、版本和下一步能力继续办理。

现代 invoke 1.1 的业务错误与连接认证分开：401 为连接凭据错误；403、404、409、413、422、503 分别映射为 `remote_forbidden`、`remote_not_found`、`remote_conflict`、`remote_input_too_large`、`remote_validation`、`remote_unavailable`。409 可从最多 4 KiB、最多 1 秒读取的 JSON 中识别固定 `error.code`：`revision_conflict`、`state_conflict`、`idempotency_conflict` 分别提供版本、状态或幂等冲突提示；未知、损坏或超限内容保持通用冲突。平台使用自己的固定文案，不显示或记录远端 `message/details`，也不把不透明对象的 404 解释为必然存在。manifest/health 与旧 invoke 1.0 保留原错误分类。

## 发布与维护

本协议已在框架 v0.4 实现，接口及行为通过单元、集成与组件测试；真实部署验收另记报告。统一身份采用本地 SQLite 会话与服务端可信调用上下文，尚未实现 OAuth/OIDC 登录或旧原生网页 SSO。

四服务的能力外壳、认证、幂等、视图与 Schema 快照放入各自 platform_integration 目录；PLATFORM_BOUNDARY.md 标明稳定边界。业务调整尽量只改业务函数与映射；协议统一升级时由统筹同时修订 Schema、样例、提示词及服务适配，并重新验收。固定页面和对话都必须覆盖声明的能力。

旧 manifest 1.0/1.1、invoke 1.0 示例独立保留；新增跨服务适配使用 manifest 1.2/invoke 1.1。跨服务业务包的格式、来源授权与业务状态需要各服务继续验证，统一协议并不自动将所有数据库合成同一数据源。

每个服务和框架独立使用本地 Git。每个经测试的调整阶段使用中文规范提交，不提交运行数据库、凭据、日志、用户文件或虚拟环境。本地运行是单进程参考部署，生产身份、备份、高可用与实际馆藏接入需要专项配置和验收。
