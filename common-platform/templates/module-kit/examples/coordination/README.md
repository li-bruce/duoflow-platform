# 并行协同：一批数据申请公开

汇交模块正在整理数据，挖掘模块同时查询已有公开数据。汇交人员提交公开申请，管理员同意后，汇交模块确认本次申请仍然有效，在自己的数据库中改成公开；协同中心随后把“这批数据已经公开”的消息交给挖掘、治理与科研服务。它们可以各自更新索引或产生通知。

`publication_demo.py` 是可运行的合成业务模块。它调用实际 HTTP 协同中心，使用自己的 SQLite 数据库。`init`、`request`、`pump` 分别演示建库、提交公开申请和收发消息；审批由主框架管理员在协同中心完成。

## 运行

在框架根目录运行，先在中心配置 `publication-demo` 及其他消费者的独立模块令牌、状态机和订阅。使用本地合成令牌，配置字段以 `contracts/coordination/v1/` 为准。示例源状态机为 `publication-demo.publication`，对象类型 `publication-demo.batch`，状态 `private / pending / public`，公开状态 `public`，需要审批；它发出 `publication.requested / state.changed / resource.published`。中心把 `publication.approved / publication.rejected` 决定直接送回来源；其他消费者声明并订阅 `resource.published / resource.withdrawn`。示例范围为 `demo-org / demo-project`。订阅由平台配置，notify 与 sync 可以分别给不同消费者。

```powershell
$env:DEMO_COORDINATION_URL = 'http://127.0.0.1:8033/api/v1/coordination'
$env:DEMO_MODULE_TOKEN = 'synthetic-publication-token-change-for-demo'
$demo = 'common-platform/templates/module-kit/examples/coordination/publication_demo.py'
$python = '.\common-platform\.venv\Scripts\python.exe'
& $python $demo init --database work/coordination-demo/publication.sqlite3
& $python $demo request --database work/coordination-demo/publication.sqlite3 --expected-revision 1
& $python $demo pump --database work/coordination-demo/publication.sqlite3
& $python $demo read --database work/coordination-demo/publication.sqlite3
```

此时对象为 `pending / revision 2`，`read` 返回 `null`。管理员批准本次请求后，再运行来源 `pump`。来源检查原申请号、原修订、对象范围以及当前本地公开条件，提交 `public / revision 3` 和 outbox；`read` 随后返回该批对象。

其他消费者分别使用自己的令牌和数据库：

```powershell
$env:DEMO_MODULE_TOKEN = 'synthetic-mining-token-change-for-demo'
& $python $demo pump --module-id mining-demo --database work/coordination-demo/mining.sqlite3
& $python $demo summary --module-id mining-demo --database work/coordination-demo/mining.sqlite3
```

`sync` 保存对象引用和修订作为索引提示，`notify` 保存通知。实际正文仍由来源模块的读取接口按当前公开状态与权限返回；接到消息只触发刷新或通知。每个消费者都能独立暂停、重启和补收。

## SDK 接入现有业务

Python：`initialize(conn)` 初始化消息表；业务事务中调用 `client.enqueue(conn, event)`；事务提交后运行 `client.relay(conn)`；通过 `client.pull()` 获取消息，再用 `client.consume(conn, delivery, handler)` 处理。handler 接收 `(conn, event, delivery)`，在该连接中同步修改业务数据。

Node：从 `sdk/node/coordination.mjs` 导入 `initializeCoordination`、`CoordinationClient`。配置使用 `baseUrl / moduleId / consumerId / acceptedSources / timeoutSeconds`；方法与 Python 相同，`relay / pull / consume` 需 `await`。Node 使用内置 `node:sqlite` 的 `DatabaseSync`，需 Node 24。

两端都要求显式 `scopes` 和可接收来源列表。outbox 保留失败次数和下次重试时间，同一对象按提交顺序发送；每次 relay 最多 20 项。消息提交到本地 inbox 后才 ACK，丢失 ACK 时重复消息直接确认。业务 handler 出错时本地事务回滚，等待中心 lease 到期重新交付。

handler 保持同步，只在传入连接中读写，不自行提交事务；网络读取或计算先准备，再在事务中复核修订与业务条件。一个 SQLite 连接由模块顺序使用，收发 HTTP 期间不让其他协程开启该连接的事务。回执表保存以支持长时间后重投；清理需要由应用结合中心保留期制定。

PostgreSQL、MySQL 等服务使用自身数据库的 outbox/inbox 适配：业务状态和 outbox 同事务，业务 handler 和 inbox 同事务。把本 SDK 的 SQLite 放在外部数据库旁边无法取得该原子性。当前随包实现覆盖 SQLite，外部数据库可保持 HTTP 协议并提供等价事务适配。

## workflow-node 模板的可选接线

新生成的 `workflow-node` 默认保留空 `coordination` 声明。准备启用时，在 `backend/registration.json` 声明真实的 `record-lifecycle` 状态机、记录对象类型及 `state.changed`，并把平台准备命令输出的模块凭据文件路径设置到 `DUOFLOW_COORDINATION_CONFIG_FILE`。凭据文件支持 `{version, module_id, url, token}`，可选 `consumer_id / interval_seconds / timeout_seconds`；`url` 可以是中心主机地址，模板自动添加协议路径。对象范围取自模块服务端项目仓库。

模板的创建、编辑、提交、审核在保存业务状态和调用回执的同一事务内写入事件；审核后的 `approved` 表示本模块审核通过。公开申请与批准使用上面的专门业务处理器。后台收发使用同一个业务 SQLite 文件的独立 WAL 连接，HTTP 在数据库事务外完成，也不占普通业务请求队列。业务正在持有写事务时，协同连接立即让出，保留消息供后续重试。

来源模块只发送消息时无需消费者。接收消息需在 `createService({coordinationHandler})` 显式提供处理函数，并在注册声明中填写对应 `accepts`。处理函数接收 `(db, event, delivery)`，业务写入和 inbox 一起提交，再确认消息；没有处理函数时模板不拉取消息。服务关闭时会等待正在进行的收发完成。

## 验证

`sdk/python/tests/test_coordination.py`、`sdk/node/tests/test_coordination.mjs` 验证回滚、持久重试、重启续传、重复消费、handler 失败、ACK 丢失、来源/目标/范围检查及真实 HTTP 超时。`test_publication.py` 验证本示例的申请修订、审批后公开及消费者效果。
