# 通用远程服务协议

新服务使用 [v0.4 集成契约](integration-contract-v0.4.md) 与 `contracts/v1.2/`：manifest 1.2、invoke 1.1。精确字段以 Schema 和实际校验器为准。`templates/module-kit/` 是推荐入口。

`templates/remote-service-template/` 是保留的回显兼容示例：manifest 1.1、invoke 1.0，不可直接套用现代 invoke 1.1 信封。

| 接口（相对于服务 base URL） | 行为 |
| --- | --- |
| `GET /platform/v1/manifest` | 返回模块、能力、输入输出 Schema、两端呈现声明 |
| `GET /platform/v1/health` | 返回服务健康状态 |
| `POST /platform/v1/invoke` | 接受版本、request_id、能力 ID/版本、input、context；返回对应标识、summary、data |

三个接口都要求服务端私有 Bearer。平台在服务端生成可信 actor；浏览器不能指定自己的角色。access v1 的范围授权另外使用短时 `X-Platform-Access` 及中央决策，服务 Bearer 不等于业务授权。

能力 Schema 的 default 不自动补入请求。服务校验输入、对象权限、状态、修订和幂等，返回符合输出 Schema 的 JSON；不能返回任意 HTML、脚本、处理器路径或执行 URL。调用在一次 HTTP 请求内完成，平台最大等待 300 秒，不自动重试不确定的副作用；本接口不把 HTTP 202、任意轮询或回调解释为已支持的长任务。

回显模板的 SQLite 事务保存结果与幂等回执；外部副作用需要服务自己的持久任务及幂等设计。新服务还需完成发现、安装前端包、注册权限、启用和授权，详见 [模块生命周期](module-lifecycle-design.md)。
# 现行接入验收补充

远程服务应增加经过同一连接认证的 GET `/platform/v1/registration` 和 POST `/platform/v1/handshake`，使用 [registration 1.0](../contracts/registration/v1/README.md)。发现成功只形成预览；全部接入检查通过后才能启用。现代与旧 invoke 的版本保持下文约定。没有验收记录的历史连接不会在启动时自动发布；已发布连接复验失败会停用。
