# 模块 AI 服务：声明、独立配置与平台选路

本目录配合 `sdk/node/ai-service.mjs`、`sdk/python/ai_service.py`。这是可运行的 SDK 使用示例，不是已经安装或授权的业务模块。Node 要求 24；Python 要求 3.11+、httpx、cryptography，使用套件锁定依赖。

在实际模块 `backend/registration.json` 的顶层声明 AI 服务，例如已有能力为 `your.module.analyze`：

```json
{
  "ai_services": [{
    "id": "your.module.analysis",
    "title": "描述分析",
    "description": "对已授权的输入生成候选分析；正式采纳由模块规则决定。",
    "capability_ids": ["your.module.analyze"],
    "supported_sources": ["platform", "module"]
  }]
}
```

将该字段并入完整注册声明；不要用这个片段替代 registration。服务 ID 属于模块命名空间，能力 ID 必须绑定实际处理器。不在声明、能力输入、图、共享组件或业务回执中填写 API URL、模型密钥和供应商配置。无 AI 的模板保持 `ai_services: []` 或不写该可选字段。新 AI 模块握手额外要求 `ai-services-1.0`；旧模块不要求。

## Node 服务接线

```javascript
import { AIServiceManager } from '../sdk/node/ai-service.mjs';
const ai = new AIServiceManager({
  services: registration.ai_services,
  dataDir: path.join(process.env.SERVICE_DATA_DIR, 'ai'),
  platformBaseUrl: process.env.PLATFORM_AI_BASE_URL,
});
const server = createModuleServer({
  manifest, registration, token, handlers, workflowHandlers,
  graph: moduleGraph, observationClient: observer, aiManager: ai,
});
```

`PLATFORM_AI_BASE_URL` 为服务端固定平台 origin，例如 `http://127.0.0.1:8030`。独立运行时传 `platformBaseUrl: null`，只使用模块自有 API。连接平台后必须配置 origin，平台不可用、票据失效、禁用或未选来源均拒绝，绝不偷偷改用模块配置。带 AI 声明的 Node runtime 在正式握手时要求实际 manager 已配置平台地址，防止漏配后把平台调用当成独立调用。握手不会自动猜测或写入部署地址。workflow-node 启动器对非空声明自动构造 manager；业务仍须在实际 handler 使用传入的 `ai`，声明本身不会调用模型。

业务 handler 使用当前票据和仓储事实：

```javascript
async function analyze({ body, ticket }) {
  const record = await repository.read(body.input.object_id);
  const scope = {
    ticket, requestId: body.request_id, operation: 'invoke',
    permission: 'your.module.analyze',
    facts: { project_id: record.project_id, organization_id: record.organization_id },
  };
  // 先按既有规则验证当前授权、正式状态、输入修订和幂等；事实来自仓储。
  const { result, route } = await ai.chatWithReceipt({
    serviceId: 'your.module.analysis', scope,
    messages: [{ role: 'user', content: record.description }],
  });
  const candidate = validateDomainOutput(result.content);
  // 期间可能发生来源读取或业务校验。紧邻提交再次核验 AI 路由及配置。
  await ai.validate({ serviceId: 'your.module.analysis', scope, route });
  // 然后由原业务事务再次核对权限、来源和对象修订，以 CAS 保存候选。
  // repository/validateDomainOutput 属于模块，SDK 不代替这些领域实现。
  return { summary: '已生成待核验候选', data: { candidate } };
}
```

只需要模型结果而不提交业务时，可用 `await ai.chat(...)`，返回 `{content,tool_calls:[],model,finish_reason?,usage?}`。`chatWithReceipt` 额外返回不含凭据的 `route`，可在最终提交前 `validate`。配置变化、中央撤权和路由切换均应拒绝旧结果；不要捕获错误后提交空结果或调用备用模型。`validate` 是当前检查，不是跨服务器的原子事务或长期授权；模块仍需本地事务/CAS/副作用恢复。

## Python HTTP 适配

```python
from sdk.python.ai_service import AIServiceManager, AIServiceError

ai = AIServiceManager(services=registration['ai_services'], data_dir=private_dir / 'ai',
                      platform_base_url='http://127.0.0.1:8030')
# 对应 Registration(..., ai_manager=ai)，固定声明和真实管理器必须一致。
receipt = await ai.chat_with_receipt(service_id='your.module.analysis', messages=messages,
    scope=dict(ticket=ticket, request_id=body['request_id'], operation='invoke',
               permission='your.module.analyze', facts=repository_facts))
await ai.validate(service_id='your.module.analysis', route=receipt['route'], scope=current_scope)
```

Python 适配器先验证服务 Bearer，再把下面固定控制路由转给 `await ai.control(service_id, method, action, body)`。不要暴露公共页面或能力用于读写这些配置。`AIServiceError.code/status` 只含安全错误，不转发供应商响应正文。Python `Registration(..., ai_manager=ai)` 校验声明和真实管理器绑定、握手特征及已配置的固定平台地址；HTTP adapter 仍须实际接入控制路由。

## 自有配置与手动选路

Node runtime 已实现仅服务 Bearer 可访问的控制接口，Python adapter 调用同一控制方法：

| 路由 | 请求/结果 |
| --- | --- |
| `GET /platform/v1/ai/services/<id>/config` | `{revision,profile}`；首次为 `0/null`，公开 profile 只有 `api_key_configured`，没有密钥 |
| `PUT /platform/v1/ai/services/<id>/config` | `{expected_revision,profile:{name,base_url,model,api_key?,clear_api_key?,enabled,temperature,max_tokens,timeout_seconds}}`，以修订 CAS 保存 |
| `POST /platform/v1/ai/services/<id>/test` | `{expected_revision}`，发送固定合成连接测试提示，返回实际供应商响应摘要 |

密钥留空保留旧值，`clear_api_key: true` 明确清除，变更 base_url 时必须重新输入或明确清除密钥。URL 仅支持 HTTPS，回环地址可用 HTTP；禁止重定向。不支持 tools、流式、多模态或自定义供应商 HTTP headers。

接平台后，管理员在平台 AI 设置中分别保存平台模型配置、模块自有配置及来源绑定，再手动选择平台或模块。初始绑定为禁用；配置存在不等于已选中。平台来源经中央 broker 调用，不将平台密钥下发模块。模块来源仍每次请求 `/api/v1/ai/resolve`，使用本地密钥执行后再请求 `/validate`；同时核对 `binding_revision` 和 `module_config_revision`。平台来源也透传 `platform_config_digest`，在中央 profile 更新后拒绝旧结果；模块来源该字段为 null。所有修订和 digest 由 resolve 固定，handler 不得从浏览器或旧业务记录伪造。

私有配置目录产生 `module-ai.config.enc` 与 `module-ai.key`，整个配置使用 AES-256-GCM 加密、随机 nonce、原子替换；密钥为本地随机 32 字节。该目录不入 Git，不随可携带源码导出。文件模式为 0600、目录0700；Windows 部署另收紧目录 ACL。复制完整目录的人仍能解密，因此不能声称本地文件密钥抵御主机或整目录泄漏。单进程管理配置，多进程需外部锁/事务存储。

独立演练可运行 `standalone-node.mjs` / `standalone-python.py`。设置私有 `MODULE_AI_DATA_DIR`，执行 `status` 查看状态；`configure` 从标准输入读取配置 JSON（避免终端历史和日志记录密钥）；`test` 才会实际调用供应商并可能计费。示例默认不会联网，不发现环境中的供应商密钥。SDK 测试中使用的 transport seam 只是合成网络回归，不能当成真实 API 或业务验收。
