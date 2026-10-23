# 可复制模块模板

本模板不默认启用。它展示最小能力：输入 `message`，返回相同的 `echo`。示例不调用模型、不写资源、不注册新 HTTP 路由。

从 `common-platform` 目录把整个 `templates/module-template` 复制到 `extensions/my-module`。在 `extensions/installed.json` 的 `modules` 数组加入 `my-module`，保留已有 `demo-text`，再重启平台。固定页面和对话中的能力目录都应出现“回显消息”；输入同一消息得到同一文本。**完成这一步无需修改核心代码。**

进一步开发时：

1. 为模块和能力换成唯一 ID；模块 ID 与能力 ID 不必等于目录名。不要与已启用模块重复。
2. 在输入 Schema 中定义参数、标题、说明和示例，在输出 Schema 中定义结果数据。示例仅使用合成内容。
3. 修改 `handlers.py`，保持 `async def handler(ctx, payload)`；通过 `CapabilityOutput` 返回摘要、结构化数据及已注册视图。
4. 两端指定相同的既有 renderer 即可复用呈现。确需新的复杂组件时先按平台组件接口实现并注册，不能通过 manifest 名称凭空生成组件。
5. 如需保存，使用 `ctx.create_resource`、`ctx.create_artifact`；如需模型，使用 `ctx.gateway`。身份、权限、幂等及事务留给平台。

模板使用 `kind=query`，不会保存领域资源；平台仍可记调用审计。创建资源/文件或产生其他副作用时改为 `kind=command`，根据持续时间选择同步或后台。新的流程/协议版本升级需要同步更新 schema、handler 和 manifest 的版本，并做两端验收。

安装清单仅面向受信任的本地开发模块。它不提供从对话上传并执行任意 Python 的能力。


模板另有显式 `surfaces`，将页面表单和对话入口映射到同一能力。修改能力 ID 时同步更新映射；本地模块仍使用本地模块契约，不需要添加远程 HTTP 的 `protocol_version`。
