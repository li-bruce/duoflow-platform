# 可复制的独立服务模板

这是无领域的 `message → text` 回显示例。整个目录可复制为自己的 HTTP 服务；它不需要平台源码，也不需要向主平台添加 Python handler。

在 `common-platform` 内复制本目录至 `examples/my-service` 后：

1. 编辑 `manifest.json`：替换 module/capability ID、标题、版本和内联 Schema。声明页面与对话两个呈现入口；`text-result` 是当前平台已支持的结果组件。
2. 编辑 `handlers.py`：返回 `(summary, data)`。不要返回平台 resource、views、任意执行地址或 handler 路径。
3. 先检查端口空闲，再运行 `start.ps1 -Port 8032`，避开本独立平台的 8030 端口。复制到平台 `examples/my-service` 时脚本默认使用本平台 `.venv`；独立部署时新建服务自己的 `.venv`，安装 FastAPI、Uvicorn、jsonschema、psutil 后通过 `-Python` 指定，不能依赖原项目环境。
4. 用本机编辑器打开本服务 `data/service-config.json`，只将 `service_token` 的值复制到平台服务接入表单。地址填写本服务监听地址；从平台发现并启用能力。整个过程无需修改平台核心。
5. 在固定页面和统一对话中分别执行同一能力，确认输入、权限、运行记录、资源和附件一致。使用本目录 `stop.ps1` 停止服务，保留缓存和配置。

`service_core.py` 包含三个标准接口、Bearer 验证、Schema 校验及 SQLite 幂等缓存；`server.py` 负责初始化随机令牌并创建应用。输入样例是合成消息。模板默认 localhost，平台注册和远程部署是两个独立步骤。

其他语言可以重写本模板，只需遵循 `../../docs/remote-service-protocol.md` 的 HTTP/JSON 协议。平台不要求远程服务使用 FastAPI。Schema 的 `default` 是表单提示，HTTP 服务不自动把它填入缺失字段。

此模板的回显操作在 SQLite 缓存事务内完成。若替换成发送消息、调用外部系统或耗时任务，需在该服务内实现相应的持久任务状态和副作用幂等；SQLite 缓存不能让任意外部副作用自动获得“恰好一次”。本轮协议只支持 300 秒内完成的请求—响应，HTTP 202、轮询和回调不属于当前接口。


模板 manifest 使用协议 1.1，`surfaces.page_tools` 显式声明平台表单，`surfaces.conversation` 声明同一能力和引导语。复制后同步修改这些 ID 映射；invoke 仍为协议 1.0。需要独立页面时可增加 `embedded` 工具，前提是页面能在无同源权限的 iframe 中运行，且不依赖平台令牌或 SSO。详见 `../../docs/module-lifecycle-design.md`。
