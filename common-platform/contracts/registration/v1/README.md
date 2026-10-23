# 模块注册与接入标准 v1

registration 1.0 是独立于 manifest 和 invoke 的接入标准。它不改变原业务调用包络，不自动创建用户授权，不安装远程代码，也不增加跨模块流程执行器。

## 文件

| 文件 | 用途 |
| --- | --- |
| `definition.schema.json` | 模块公开数据、业务步骤、权限、呈现依赖与实际对象接口 |
| `offer.schema.json` | 平台发送的完整协议内容、支持项、随机编号和摘要 |
| `confirmation.schema.json` | 模块检查本地内容后返回的确认 |

主框架校验源为 `app/registration_contract.py`；传输验收由 `app/remote_services.py` 执行；平台依赖与发布由 `app/runtime.py` 检查。模块 SDK 为 `templates/module-kit/sdk/{python,node}/registration.*`。完整双方说明见 `docs/主框架说明.md`、`docs/模块框架说明.md`。

## 声明规则

1. Schema 不允许未知字段。所有数组必须提供；没有公开内容时使用空数组，不能凭空填入尚未实现的功能。
2. 模块身份和版本与 manifest 完全一致。能力、公开对象、业务步骤编号属于模块命名空间，编号唯一。
3. 每个对象至少登记一个真实操作。list/read 引用查询能力，create/update/delete 引用命令能力；只能引用本模块现存能力。
4. 每个对象提供合法、受限深度的 Schema；所有引用只能是本地片段，不读取外部 Schema。
5. 业务步骤入口等于第一步，每一步引用实际能力；它是公开顺序声明，不意味着平台自动执行或完成整个业务。
6. 有正式对象时必须提供 access v1 权限附件，完整覆盖能力，scope_fields 来自附件 scope_keys。权限附件的办理流程必须列入所需宿主流程。
7. 平台已注册范围权限的模块不能通过改用固定角色声明绕过它。实际用户授权独立管理。
8. 所需组件与流程必须由管理员安装并固定版本；不能在握手时下载代码。模块声明的对象接口清单须与实际分派表一致。

## 接口与确认

全部接口先验证服务连接凭据。固定路径为 `/platform/v1/manifest`、`registration`、`health`、`handshake`、`invoke`，以及声明的 `workflow/{operation}`。无凭据和错误凭据返回 401 或 403；正确凭据下的空 POST 请求返回 400 或 422，且不能执行业务。

平台 offer 携带完整 manifest 与 registration。模块先核对协议、完整文档、真实能力/对象函数表与输入输出 Schema，再确认。回应中的 `checks` 五项均须为布尔 true，能力编号按字母排序；workflow_operations 与声明一致并按字母排序。错误内容使用固定消息，不保存或回显远端细节。

摘要是平台内部排序 JSON 的 SHA-256。模块不需重新序列化计算摘要，而是比较完整文档后返回收到的摘要和当前随机编号；这不构成数字签名。请求和回应仍依赖已配置的私有连接认证，非回环网络使用 HTTPS。

## 验收与发布

七项报告按顺序为 manifest、registration、authentication、health、invoke、module_confirmation、host。全部真实执行并通过才为 passed；后续未检查项不能记为通过。

保存/修改连接、发现、验收、发布是不同操作。验收不自动发布；五分钟内可提交发布，发布再次检查平台依赖。变更地址/凭据/配置会清除旧验收。已发布服务复验失败或注册内容改变时停用并改变连接修订。并发连接编辑、管理员撤权或第二次握手不能把旧结果覆盖为当前成功。

旧清单可以继续发现，但没有本标准验收记录的连接启动时不发布。旧 manifest 1.0/1.1 也可以补独立握手；现代范围权限仍要求 manifest 1.2 / invoke 1.1。

## 证据边界

这些检查证明声明结构、身份拒绝、接口拒绝、模块确认及平台安装依赖符合标准。它们不证明所有业务算法、对象范围事实、字段政策、真实模型或生产环境都正确。业务验收使用模块自己的合成记录和完整用户流程，不在握手中试写真实数据。

## 未通过时怎样处理

| 检查编号 | 处理方法 |
| --- | --- |
| `remote_admission_credentials` | 给连接配置模块的私有服务凭据；无认证服务不能通过验收 |
| `remote_manifest_changed` | 重新发现并核对当前能力清单，再完整验收 |
| `remote_admission_interface` | 检查固定路由是否存在，身份验证是否先于业务，空请求是否被拒绝 |
| `remote_admission_health` | 修复模块健康状态，再检查；心跳恢复不会自动发布 |
| `remote_admission_contract` | 对照失败项目检查 Schema、编号、版本、引用和函数绑定；先运行离线 check |
| `remote_admission_host` | 安装匹配版本的组件/流程、注册相同权限附件，再完整检查 |
| `remote_admission_required` | 尚未全部通过或验收超过五分钟；重新检查后发布 |
| `remote_admission_busy` | 当前连接的上一次检查尚未结束，不同时发起第二次 |
