# Node.js 与其他语言的统一权限判断

主框架对已注册 access definition 的远程调用额外发送 `X-Platform-Access` 短时票据。原 manifest / invoke / actor JSON 不变；旧 actor.roles 不用于表达新模块角色。票据含最终用户授权快照、模块能力与连接修订、请求号和操作，由主框架 HMAC 签名，最长 60 秒；默认进程密钥使重启前票据失效。

1. 模块先验证已有连接 Bearer，按原协议读取 request_id 和 actor；拒绝未经认证的调用。
2. 从模块自己的数据库读取对象事实，例如 project_id、owner_id；不能把浏览器提交的 owner_id 当事实。
3. 用部署时配置的主框架 callback URL 创建 `createAccessClient`，调用其返回函数：

```js
const decide = createAccessClient({
  callbackUrl: process.env.PLATFORM_ACCESS_DECISION_URL,
});
const decision = await decide({
  ticket: request.headers['x-platform-access'],
  requestId: body.request_id,
  operation: 'invoke', // workflow 适配使用对应 objects.read 等真实 operation
  permission: 'example-module.item.read',
  facts: { project_id: row.project_id, owner_id: row.owner_id },
});
if (!decision.allowed) throw new Error('当前无权办理');
```

4. 允许后，模块仍在事务内检查业务状态、预期修订、幂等和职责分离。提交前重新判断，避免把一次允许当永久授权。

票据不传入网页、模型、日志或持久化业务数据；callback URL 仅来自服务端配置，不能采用表单或微服务请求指定的地址。示例禁止重定向，限制请求、响应体积和超时，非 2xx / 无效响应 / 连接失败均拒绝，不能降级为旧角色允许。

票据仅允许其能力已登记的权限，不可切换用户、模块、连接、请求或操作。facts 的可信性来自已受控接入的业务服务，桥接不能防止一个已被攻陷或恶意的权威模块谎报自身对象事实；这与模块直接运行授权代码处于相同信任边界。服务对自己的 facts 负责，主框架对身份、授权和票据有效性负责。

当前默认随机密钥适合单主框架进程。多 worker / 多副本部署须统一配置安全签名密钥或集中决策入口，并处理密钥轮转；否则分发到另一进程的票据会拒绝。此票据是单次调用内的短时决策通道，不是 OAuth 登录、第三方通用访问令牌或长任务委托协议。

`example-service.mjs` 是无需额外 npm 依赖的 Node 只读服务样例，提供 manifest 1.2、health 和 invoke 1.1。部署变量为 `PLATFORM_SERVICE_TOKEN`、`PLATFORM_ACCESS_DECISION_URL` 和可选 `PORT`（默认系统分配端口），仅处理两条合成记录。主框架需注册 example-access 权限包并单独授予项目/本人范围后才能读取。`ACCESS_EXAMPLE_SELF_TEST=1` 仅在自动验收中开启，增加受服务凭据保护的内存诊断；默认关闭。实际集成测试见 `tests/test_remote_access_node_e2e.py`，测试生成临时凭据与随机空闲端口，不占用现有演示服务。
