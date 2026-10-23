# Python 原生权威对象与共享步骤模板

这是中立的“私人记录”示例，不含汇交字段、文件上传、长任务、审核规则或模型调用。Python 模块自己的 `example_workflow_notes` 表是唯一业务状态；平台 Flow 只持有 object_ref、个人草稿和导航。它证明原生 Python 可直接作为流程业务权威，不需要另一套 Node 服务。

受信任模块的 manifest 可选顶层字段 `workflow_adapter: "adapter:create"`。只能引用同一扩展包内简单 Python 文件名与工厂符号，不支持 URL、路径、动态代码或远端 manifest。`Registry.load` 加载 factory；Workflows 初始化时调用 factory(context)。context 为不可变 `LocalWorkflowContext`，仅含 sessions、模块专属 data_dir、module_id，不提供 runtime、模型网关或请求身份。模块仍处于现有受信任本地 Python 扩展边界，不是沙箱。

factory 返回带 `request(*, actor, capability_id, operation, payload, request_id)` 的对象，可同步或异步返回数据对象。可信 actor 由平台每次重新授权后注入；业务模块继续执行对象 ACL、状态与修订守卫。返回对象与远端 workflow v1 data 完全一致，平台同样检查字段、大小、对象匹配和秘密泄露。可选 upload_chunk 方法未实现时返回 501；此模板 request 仅支持 objects.list/read，其余操作明确返回 501。

objects.list 接受可选 object_type、cursor、limit，默认每页 20 条，最多 50 条，返回 `{objects,next_cursor}`；本模板只接受自身对象类型。列表 data 仅含标题摘要，选中后的 objects.read 才返回正文。游标绑定本人、授权修订及记录列表修订快照，列表变化返回 409 后从首页重读。模块仍在每页重新验证权限，游标不授予访问权。单次响应 data 预算为 list 128 KiB、read 1 MiB、detail 512 KiB；整体适配响应和最终 Flow 视图仍最多 2 MiB。超出预算明确报协议错误，不由平台暗中删掉授权动作。大型详情应另实现 objects.detail 按 section 分页，此模板没有详情引擎，明确返回 501。旧本地适配器可暂省 next_cursor，平台按 null 处理；远端适配器必须提供它。

安装时将 backend-python 内容复制到 `extensions/<目录>/` 并加入 extensions/installed.json 的 modules；将 flow 内容复制到 `flows/packs/<目录>/` 并加入 flows/installed.json 的 packs。按需重命名模块、能力、flow ID 和模块表名，保持引用对应。前端使用平台 React 与 `@platform/workflow-sdk`，安装后执行类型检查、生产构建并重启。普通用户不能上传或注册任意 Python 模块。

create/edit/read/list 四个能力也可直接通过既有 invoke 接口使用。create/edit 使用 ctx.session 的同一事务写入模块表和审计；模块不自行 commit，平台 invocation 的幂等回执与业务写入一并提交。edit 使用 owner + expected_revision + draft 状态的 CAS，冲突回滚，不用 UI 草稿直接覆盖。重启后列表和 object_ref 读取同一表；重复原调用返回原回执，不再次建对象。后续新增对象状态机应仍以该表或明确的模块存储为真源，不再镜像远端状态。

共享组件映射为 Picker/Editor：完整页面可按步骤排列，对话只显示当前步骤；两者通过相同 execute/openObject/updateDraft API 工作。组件没有 fetch、登录、服务地址或独立聊天。模板源码提供两个宿主可安装的定义和组件；实际生产浏览器联调需在安装后单独验证，不能仅凭这些文件宣称已完成某业务的浏览器验收。

流程可声明 page_groups 将多个已注册步骤组成完整页面，省略时仍为单步骤。步骤定义与 command_schema 来自 catalog；实例响应的 step_actions 仅含当前授权动作摘要，actions 保留当前步骤摘要。组合页面不能跨过当前 cursor 执行动作，宿主须先按正常修订守卫切换个人导航再执行，不能改变后端能力、对象或状态守卫。
