# 可注册范围授权 v1

`definition.schema.json` 是独立权限包；不可向已有 manifest 或远程 actor 随意添加字段。`binding.schema.json` 是主框架受控管理入口创建的实际用户授权。模块安装仅注册定义，不携带真实人员授权。

每个权限、角色模板、办理视图与待办源 ID 以 `module_id + '.'` 开头；能力映射仅绑定本模块。模块命名空间不得相互包含。scope 的所有键值同时匹配可信仓库 facts，每一条授权独立判断，不能跨授权拼接范围。facts 值可为非空字符串，或表达已验证关系的非空字符串数组；浏览器 payload 和模型输出不是可信事实。

角色 `required_scope` 至少有一个范围维度，不接受 `*` 通配授权。已授予角色保留权限快照，并与当前模板权限取交集；升级新增权限需另行授权，收紧立即生效。职责分离 guards 对最终权限统一生效，双角色不能绕过。禁用、撤权、过期、缺失事实和未知动作默认拒绝。业务状态机仍由模块实现，授权允许不等于状态转换成功。

`experiences.required_permissions` 要求全部满足，`step_ids` 为可选的可见步骤集合。无对象的目录只检查有效授权存在；执行仍需 fresh facts。`workitem_sources` 是聚合元数据，不自动运行代码。列表查询必须先落实授权范围再分页、计数和统计。

Python 模块使用 `ctx.access.check(ctx.actor, permission, trusted_facts)`；Node 或远端 Python 通过受认证的适配接口取得等价决定，不能由浏览器直接提交授权 facts。当前角色目录与 invoke actor 无需私加新角色字段，动态角色由独立注册表保存。

`AccessControl(sessions)` 的管理接口仅供已验证 `identity.manage` 的主框架管理入口调用；核心不接受客户端自称管理员。决策方法只读，不另开写审计事务。`AccessControl.from_session(session)` 借用业务事务，禁止管理写入，也不会提交调用者事务。

真实引擎为 Casbin 1.43.0，使用框架内固定的 RBAC domain 模型：每条完整授权作为一个 domain，由框架校验 scope 与 guard 后交给 Casbin。模块不能注入匹配表达式。SQLite 保存定义、角色绑定、到期与撤销、修订及管理审计；引擎可经同一门面替换。Casbin 不替代登录、列表范围过滤、正式状态机、幂等或事务检查。

`authorization_stamp(actor)` 绑定当前策略定义修订及此人的有效授权。其他人的角色分配不会打断当前人的流程；本人撤权、到期、定义变化、禁用再启用均使旧实例授权快照失效。长任务和分布式提交仍需要明确的提交点与再次校验，不能把一次允许决定当永久通行证。

接口主要方法：`register_definition`、`grant`、`revoke`、`set_enabled`、`definitions`、`bindings`、`check`、`check_command`、`has_permission`、`matching_scopes`、`capability_permission(s)`、`experiences`、`module_registered`、`authorization_stamp`。`has_permission` 只供目录；`matching_scopes` 是 OR-of-AND 的完整范围组，查询适配层不能扁平合并。

`command_mappings.command` 用于字符串子命令选择器。workflow v1 的 `input.command` 是结构化事务信封，应为每个具体能力配置无 command 的权限映射；结构化信封不会被当作子命令名称。未知字符串子命令拒绝，不能回退到普通能力权限；同一个能力不能混合无条件映射和字符串子命令映射。

开源资料：[Casbin 域内 RBAC](https://casbin.apache.org/docs/rbac-with-domains/)、[PyCasbin](https://github.com/apache/casbin-pycasbin)。
