# Python 工具 SDK 1.0.0

可选 `ai_service.py` 独立提供 `AIServiceManager`（httpx + cryptography），不依赖平台 app 包。注册声明使用 `ai_services`，既有 `module_kit.py` 纯工具入口不自动启用模型。独立运行使用自有 API，接平台后每次按中央绑定选路，禁止静默 fallback；`chat_with_receipt` / `validate` 支持在最终业务提交前重新核对配置、来源和授权。适配器须先验证服务 Bearer，再接 manager 的 `control` 方法，实际 manager 声明须与注册声明一致。详见 [AI 服务示例](../../examples/ai-services/README.md)。

`graph_testing.py` 提供 `assert_transition(graph, machine_id, before, event, after)` 与 `assert_atom_result(graph, atom_id, inputs, output, validate)`。测试先调用真实业务代码，再传入实际读到的对象或结果。后端原子另传 `manifest=实际清单`，函数按能力 ID 和版本读取 Schema。`validate(schema, value)` 由项目提供，校验失败时抛出异常。前者核对同一对象的状态变化是否有声明，后者核对原子输入输出；权限、修订和实际数据效果仍需独立断言。

已有 Python HTTP 服务另使用同目录 `registration.py` 主动核对注册握手，以及 `remote_access.py` 请求中央授权。注册确认器不替代 HTTP 认证或实际调用适配器，使用方法见同批 `docs/模块框架说明.md`。原生工具不进行远程握手。

`module_kit.py` 为标准库单文件，脚手架复制为生成包的 `backend/_module_kit.py`。安装时整个 backend 目录一起复制。没有 PyPI 安装、全局路径修改或对本仓库工作目录的依赖。

稳定入口：

```python
run_tool(ctx, permission="your.module.use", payload=payload, handler=business_function)
require_organization_access(ctx, "your.module.use")
```

`ctx` 必须来自平台，提供认证后的 `actor` 与真实 `access.check(actor, permission, facts)`。本 SDK 只读取认证身份中的 `organization_id`，采用 `allowed is True` 默认拒绝，不包含 Casbin、角色回退或本地授权缓存。业务函数接收 JSON 对象，返回 `{"summary": "说明", "data": {...}}`；输入上限 100000 UTF-8 字节，结果上限 256000 字节。`ValueError` 映射为通用输入错误，其余业务异常交给平台安全错误处理。`KitError` 提供 code、message、status，由原生处理器转换成平台错误。

该入口仅用于没有持久对象的机构内纯函数工具。正式对象、仓储事实、事务、职责状态、修订、幂等和副作用不由它实现；不要用操作者机构替代已有对象的机构。平台仍验证能力输入输出 Schema、访问快照和宿主请求安全。复制后 SDK 是模块发布的一部分；框架升级不会自动改写已交付副本，升级要对照契约、替换 SDK 并测试。
# Registered source reads

`source_reads.SourceReadClient` supports the optional `source-reads-1.0`
callback. It validates a fixed deployment callback, bounds IO and returns no
credentials. Use the current consumer ticket and repository facts. Sources are
resolved through registered read-only object adapters; domain validation and
future authorization remain the business module's responsibility.

`SourceReadError.code` and `.status` preserve safe error classification, including
403 authorization, 409 version conflict, 422 contract and 503 unavailability.
The exception text contains only the safe code, never private response messages.
