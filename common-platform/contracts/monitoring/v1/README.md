# 独立技术监控协议1.0

监控中心是独立FastAPI/Uvicorn进程和SQLite，主平台只取得逐服务聚合快照。技术监控不能调用业务能力或授予用户权限。业务运行观察1.0的单次调用事件、当前授权与正式对象引用保留原通道。

| 接口（独立监控地址） | 权限与内容 |
| --- | --- |
| `GET /health` | 服务存活及collector_id，不含目标列表 |
| `PUT /api/v1/monitor/targets` | 平台控制Bearer、owner_id与CAS；同步最多256个已登记目标 |
| `GET /api/v1/monitor/snapshot` | 平台控制Bearer；只返回白名单状态、计数与最新值汇总，无原始报告、地址和令牌 |
| `POST /api/v1/monitor/targets/{id}/probe` | 平台控制Bearer；在独立进程中探测固定健康接口，不执行能力 |
| `GET /api/v1/monitor/targets/{id}/reporting` | 平台控制Bearer；取得本服务独立报告凭据，仅写模块私有配置，不交浏览器/对话 |
| `POST /api/v1/monitor/reports/{id}` | 对应服务的报告Bearer；不能管理目标、读取汇总或冒用其他服务 |

主框架管理接口`GET /api/v1/remote-services/monitoring/summary`只读进程缓存，需要当前平台管理员；普通服务列表同样携带汇总。浏览器刷新不增加底层采集频率。正常主API构造只使用独立客户端；显式注入RemoteServiceManager的单元测试可使用原本地探测驱动，部署没有隐式本地降级。

主平台每5秒拉取一份至多1MiB完整快照，刷新合并；超过20秒未取得有效汇总为过期，连接故障退避到最多30秒。读取失败不把旧快照标成新数据，也不把所有模块判离线。实际业务调用仍执行原授权、修订及HTTP错误检查。

监控中心每15秒目标周期探测固定`base_url/platform/v1/health`；单次整体5秒、4并发、连续失败3次offline，45秒未完成探测stale。目标多且普遍超时时实际周期可能超过15秒，保持有界并发并显示真实过期，不承诺未经压测的规模SLA。服务发布与健康独立；一次接入验收读取健康接口属于正式接入检查，不属于后台监控流量。

报告最多8KiB，每实例每秒1次；每服务最多16实例。实例标识、boot_id与单调序号用于去重/乱序检查；相同重发不更新新鲜时间。每实例仅存最新值，长于1小时的身份清理；退休启动批次也有上限，不是无限历史或审计日志。重启不把旧采样时间更新为现在。

可选指标：归一到主机可见核数的进程cpu_percent（0—100），rss_bytes，inflight_requests，queue_depth，requests_total，errors_total。计数含义由模块注明；默认Node模板统计HTTP请求，队列深度须运行器真实提供。缺失指标不填0。只聚合45秒内新鲜实例；CPU取均值，其他指标求和；仅所有新鲜实例均提供该项时返回数值，否则null。计数是当前新鲜进程累计，不是跨重启历史总量。

探测凭据沿用管理员配置的模块连接Bearer，独立加密保存，固定只访问健康路径。报告凭据每服务独立生成，加密保存、存摘要验证；更换目标地址/探测凭据时旋转报告令牌并清空旧报告。仅发布修订变化可保留同端点报告和健康时间，不伪造新鲜。全批配置事务提交后才更新内存；旧连接的在途响应不能覆盖新修订。

Node`sdk/node/telemetry.mjs`与Python`sdk/python/telemetry.py`直接报告独立地址，最多1在途，不积累无界队列、不立即重试，失败不影响业务。配置独立于AI、业务访问和业务观察票据。未接入报告的旧模块只获得健康，负载保持未知。

Schema由实际Pydantic模型生成，运行`scripts/build_monitor_contract.py --write`及`--check`；不读取真实配置。设计和运行步骤见`docs/独立监控服务设计.md`。本协议为本地候选，不代表部署Celery、Prometheus或OpenTelemetry，不提供多节点高可用或业务事务保证。
