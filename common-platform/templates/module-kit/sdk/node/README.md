# Node 模块 SDK v1

这是可随模块交付的服务端代码，要求 Node.js 24。生成器把本目录复制到生成包的 `sdk/node/`。不依赖 npm 安装，不导入主框架内部 Python，也不包含浏览器 SDK。

稳定边界：

测试辅助文件 `graph-testing.mjs` 提供 `assertTransition(graph, machineId, before, event, after)` 与 `assertAtomResult(graph, atomId, inputs, output, validate)`。先运行真实业务函数，再用实际结果核对状态边与原子输入输出。后端原子另以第六个参数传入实际 manifest，按能力 ID 和版本读取 Schema。`validate(schema, value)` 校验失败时应抛出异常。模板的 HTTP/SQLite 测试已把真实提交、审核结果与状态图核对，权限及数据库结果仍分别断言。

| 接口 | 职责 |
| --- | --- |
| `createModuleServer({manifest, registration, token, handlers, workflowHandlers})` | 固定 HTTP 路径、恒定时间 Bearer 校验、64 KiB 输入、invoke 1.1 / workflow 1.0 包络、能力输入输出校验和安全错误；通过真实函数表分派 |
| `createRegistration(...)` | 启动时检查函数完整覆盖；握手时核对平台支持与完整声明后确认。不会调用业务函数 |
| `createAccessClient({callbackUrl, timeoutMs, fetchImpl})` | 向服务端配置的固定中央地址请求授权；票据缺失、超时、重定向、异常或无效回复均拒绝 |
| `Store.transaction(callback)` | 单连接串行任务及 SQLite 原子提交；业务对象和成功回执在同一事务中保存 |
| `Store.receipt/saveReceipt` | 请求编号绑定整个请求指纹，不包含传输头；重放前由业务重新授权 |
| `Store.cursor/offset` | HMAC 游标与调用人、授权修订、目录快照绑定；游标本身不授予权限 |

`invoke({body,ticket})` 返回 `{summary,data}`。`workflow({body,operation,ticket})` 返回适配器 `data`。`ticket` 只在当前内存调用栈使用，不返回、记录、写入仓储或传给前端。`facts` 必须来自服务端仓储；本 SDK 不会把输入自动变成可信事实。

`compile` 是明确封闭的 Schema 子集，支持本模板所需的 type、properties、required、布尔形式 additionalProperties、items、长度/数值范围、uniqueItems、pattern、enum、const 及说明字段。它不是完整 JSON Schema 引擎；Schema 形式的 additionalProperties、`$ref`、组合条件、format 等未支持内容在启动时拒绝。字段只按对象自身属性匹配，constructor、toString 和 __proto__ 不具有特殊验证能力。业务需要完整 Draft 2020-12 时，应以锁定版本的成熟验证库替换此适配点，并保留测试；不能静默忽略不认识的约束。

当前事务参考只面向一个 Node 进程和本地 SQLite。不要把持锁期间的中央回调扩展成长计算或外部写入；分布式副作用需要 outbox/inbox、领域幂等和恢复设计。本 SDK 不提供登录、角色引擎、游客、SSO、通知、长期委派、文件存储、长任务或聊天分支；这些能力不能通过添加同名配置字段声称实现。

SDK 升级随包显式发布，不从网络动态加载代码。已有独立服务可以只复用 `access-client.mjs` 和固定传输边界，保留原数据库与业务事务；不要求迁移到参考 `Store`。
