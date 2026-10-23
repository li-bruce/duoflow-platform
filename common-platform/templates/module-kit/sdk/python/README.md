# Python 工具 SDK 1.0.0

已有 Python HTTP 服务另使用同目录 `registration.py` 主动核对注册握手，以及 `remote_access.py` 请求中央授权。注册确认器不替代 HTTP 认证或实际调用适配器，使用方法见同批 `docs/模块框架说明.md`。原生工具不进行远程握手。

`module_kit.py` 为标准库单文件，脚手架复制为生成包的 `backend/_module_kit.py`。安装时整个 backend 目录一起复制。没有 PyPI 安装、全局路径修改或对本仓库工作目录的依赖。

稳定入口：

```python
run_tool(ctx, permission="your.module.use", payload=payload, handler=business_function)
require_organization_access(ctx, "your.module.use")
```

`ctx` 必须来自平台，提供认证后的 `actor` 与真实 `access.check(actor, permission, facts)`。本 SDK 只读取认证身份中的 `organization_id`，采用 `allowed is True` 默认拒绝，不包含 Casbin、角色回退或本地授权缓存。业务函数接收 JSON 对象，返回 `{"summary": "说明", "data": {...}}`；输入上限 100000 UTF-8 字节，结果上限 256000 字节。`ValueError` 映射为通用输入错误，其余业务异常交给平台安全错误处理。`KitError` 提供 code、message、status，由原生处理器转换成平台错误。

该入口仅用于没有持久对象的机构内纯函数工具。正式对象、仓储事实、事务、职责状态、修订、幂等和副作用不由它实现；不要用操作者机构替代已有对象的机构。平台仍验证能力输入输出 Schema、访问快照和宿主请求安全。复制后 SDK 是模块发布的一部分；框架升级不会自动改写已交付副本，升级要对照契约、替换 SDK 并测试。
