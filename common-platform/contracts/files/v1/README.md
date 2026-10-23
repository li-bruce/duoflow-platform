# 文件传输适配 v1

本契约新增同源上传通道，不改变现有 `platform-file` 的 4 MiB 内联接口。文件只在业务服务的独立存储适配器中保存；平台不复制上传、正式附件、锁定或业务版本状态。当前支持远端 HTTP 适配器，以及受控 extensions manifest 通过 `workflow_adapter: "adapter:create"` 安装的 Python 原生适配器工厂。工厂收到 sessions、模块专属 data_dir、module_id；当前身份由每次调用另行注入。平台没有默认本地文件仓库，模块未实现文件操作或 upload_chunk 时明确返回 501。

平台路由均位于 `/api/v1/flows/instances/{instance_id}`，先由服务端加载并授权流程实例，从已安装定义取能力，再解析其业务对象引用。请求不能指定 capability、目标 URL、身份上下文或 object_ref。所有写入需登录和 CSRF，GET 需登录。执行前后重新核验当前身份、能力权限、服务可用性与配置修订；业务服务仍须独立验证对象范围、上传所属对象及业务锁定。

| 平台接口 | 输入 | 业务服务固定接口 |
|---|---|---|
| `POST /uploads` | filename、size、sha256、expected_revision、idempotency_key | `POST /platform/v1/workflow/uploads.create` |
| `GET /uploads/{upload_id}` | 路径编号 | `POST /platform/v1/workflow/uploads.status` |
| `PUT /uploads/{upload_id}/chunks/{index}` | 原始二进制、可选 X-Chunk-SHA256 | 同路径后缀 `/platform/v1/workflow/uploads/{upload_id}/chunks/{index}` |
| `POST /uploads/{upload_id}/complete` | expected_revision，可选 idempotency_key | `POST /platform/v1/workflow/uploads.complete` |
| `POST /uploads/{upload_id}/abort` | expected_revision，可选 idempotency_key | `POST /platform/v1/workflow/uploads.abort` |

JSON 采用 `contracts/workflows/v1/adapter.schema.json` 的包络；平台注入 input.object_ref。create 的稳定幂等键由界面为一次文件选择生成，网络重试保持它和原参数不变。传输层 request_id 每次请求生成，用于响应匹配，不能代替业务创建幂等键。

分块必须 `application/octet-stream`，不接受压缩编码，单块最多 1 MiB。分块索引从 0 开始；服务端要求顺序写入，同索引同哈希可安全重试，不同内容返回 409。代理注入服务 Bearer、X-Request-ID，以及 X-Platform-Context（UTF-8 JSON 的标准 base64，内容为 protocol_version、可信 actor、object_ref）；该头仅为小型身份元数据，不含文件。浏览器不能提供或覆盖这些远端头。

主 HTTP 中间件只对 `is_upload_chunk_request(path, method)` 精确匹配的 PUT 路由跳过整体缓冲，保留声明长度、Origin 与安全头检查；路由逐段读取并核验实际长度，超限不向远端发送。其他接口继续原有大小限制。代理 JSON 请求最多 256000 字节，响应最多 2 MiB，不接受重定向、HTTP 202、压缩响应、重复 JSON 字段、内联 content_base64 或已配置服务凭据。

create/status 返回 `{upload, projection}`；chunk 返回 `{upload}`；complete 返回 `{file, projection}`；abort 返回 `{projection, aborted:true}`。正式文件形状见 file-ref.schema.json，上传会话形状见 upload.schema.json。服务端重新计算总长与 SHA-256，完成后原子关联正式文件；描述中的客户端 size/hash 不构成完整性证据。会话状态为 uploading、complete、aborted、failed；失败不能冒充已完成，应查询状态并明确中止或重传。

任意总量、文件数、包内结构或解包额度由业务定义和存储适配器实施。此通道与小型合成测试不构成 2 GB 或更大文件的容量验收；断点恢复需要读取已收到的分块和重新选择同一个本地文件，不在消息、模型或 UI 草稿中保存文件字节。
