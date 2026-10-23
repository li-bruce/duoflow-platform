# 第一版实现接口约定（开发协作）

此文保留第一版接口设计供兼容参考，现行架构、角色与安装状态以 docs/通用框架架构与代码地图.md、contracts/、实际校验器和生成提示词为准；下述历史 Demo 与协作角色不表示独立包安装了这些业务。本独立 common-platform 后端 FastAPI + SQLAlchemy（本地 SQLite），前端 React/TypeScript，默认监听 127.0.0.1:8030。API /api/v1，前端生产构建由后端同源提供。全部 JSON 错误 {error:{code,message,details?}}。

## 通用 HTTP 接口

- POST /auth/login {username,password} -> {user:{id,username,name,role},csrf_token}; HttpOnly session cookie。GET /auth/session -> 同结构或401。POST /auth/logout。
- 初始本地演示身份：admin / demo-admin-2026；member / demo-member-2026。仅本地监听，README 明确演示密码。角色 admin/member。
- 所有登录后写请求带 X-CSRF-Token；前端不能提交 actor/workspace/role。配置页仅admin可访问。
- GET /registry -> {modules:[{id,version,name,description,capabilities:[CapabilitySpec]}],capabilities:[CapabilitySpec],renderers:[...]}。过滤当前用户无权能力。
- CapabilitySpec: {id,version,title,description,module_id,kind:'query'|'command',input_schema,output_schema,presentation:{page:{renderer},conversation:{renderer}},execution:{mode:'sync'|'background',confirmation:boolean},roles:['admin','member']}。
- POST /invoke {capability_id,input,idempotency_key,expected_revision?,conversation_id?,origin:'page'|'conversation'} -> InvocationResult。
- InvocationResult {id,capability_id,invocation_status,run_status?,run_id?,summary,data?,views:[{type:'text-result'|'resource'|'task'|'form'|'model-settings'|'history',data:...}],artifacts:[],resource?,error?}。
- GET /runs -> {items:[Run]}；GET /runs/{id} -> Run；POST /runs/{id}/retry, /cancel。
- Run {id,capability_id,status,input,result?,error?,created_at,updated_at,attempt,progress}。状态 queued/running/waiting_human/succeeded/failed/cancelled。
- GET /resources -> {items:[Resource]}；GET /resources/{id} -> Resource；POST /resources/{id}/decide {decision:'accept'|'return',note,expected_revision}。
- Resource {id,type,title,revision,status:'draft'|'in_review'|'accepted'|'returned',data,created_at,updated_at,versions:[]}。
- GET /artifacts/{id}/download（登录及归属验证）。GET /audit -> {items:[{id,action,target,actor,created_at,detail}]}。
- POST /conversations {} -> {id,title,messages:[]}；GET /conversations -> {items:...}；GET /conversations/{id} -> {id,title,messages:[{id,role,content,views,created_at}]}；POST /conversations/{id}/messages {content,model_profile_id?,idempotency_key} -> {conversation_id,message:{...}}。响应最终消息；前端显示真正等待状态，不伪造token流。
- POST /conversations/{id}/fork {message_id?} -> 新会话；只复制讨论及引用，不重放业务动作。
- GET /health -> {status,profile,registered_modules,model_configured,...}。

## 模型配置服务（model_gateway.py + model_routes.py 独立负责）

模型agent先实现自有服务类 ModelGateway(data_dir:Path)，不依赖主数据库。密钥加密存放在 data_dir/model-profiles.json，主密钥放同目录受保护文件或Windows DPAPI；页面只返回 api_key_configured，不返回原值/密文。写配置不进入通用调用的原文日志。

- GET /models -> {items:[ModelProfilePublic],default_profile_id}。
- POST /models {id?,name,base_url,model,api_key?,clear_api_key?:false,enabled,temperature,max_tokens,timeout_seconds,is_default?} -> 公共profile。
- DELETE /models/{id}；POST /models/{id}/test -> {ok,message,latency_ms,model,response_preview?}。
- ModelProfilePublic {id,name,base_url,model,api_key_configured,enabled,temperature,max_tokens,timeout_seconds,is_default}。
- OpenAI兼容 /chat/completions。base_url填 /v1前缀，gateway附加/chat/completions。支持多个profile、默认选择、本地无key端点。保存不触发远程请求；测试使用固定合成文本；删除/禁用不会泄露旧key。
- async gateway.chat(messages:list[dict],profile_id:str|None=None,tools:list|None=None) -> {content:str,tool_calls:list,model,usage?}。异常 ModelGatewayError(code,message)。不把密钥、供应商原始错误body暴露UI，timeout可解释。主API通过 admin依赖 + CSRF 保护管理路由。model_routes 使用主项目 app.security.require_admin/require_user/check_csrf；如冲突先仅提供 create_model_router(gateway,require_admin,require_user,check_csrf) 工厂。

## 模块和处理器契约

- app/contracts.py 由根代理提供：CapabilityContext(session,actor,workspace_id,gateway,run_id=None)，提供 create_resource(type,title,data)->dict、create_artifact(resource_id,name,content,media_type)->dict。CapabilityOutput(summary,data,views=[],resource=None,artifacts=[])。handler可以async。
- 每个模块位于 extensions/<name>，含 manifest.json、schemas/*.json、handlers.py、README.md。installed.json显式列出启用模块路径。加载器只加载这份受控清单中的本地包，不支持用户上传任意Python。
- manifest {id,version,name,description,capabilities:[{id,version,title,description,kind,input_schema:'schemas/x.json',output_schema:'schemas/y.json',handler:'handlers:function',roles:[...],execution:{mode,confirmation},presentation:{page:{renderer:'text-result'},conversation:{renderer:'text-result'}}}]}。
- Demo module ID demo.text：能力 demo.text.clean 归一空格/去空行/可选行去重并给字符/行/词频统计，保存resource和可下载txt；demo.text.summarize 使用gateway真实模型输出摘要，background。input {title,text,deduplicate?,model_profile_id?}。output {text,stats?,model?}。中文文案由模块声明，不在核心写demo字段。
- 通用资源送审 core.resource.submit(input resource_id,expected_revision) 经 /invoke；管理员决定接口同样在对话卡能操作。作为轻量持久人工待办，不声称已部署Temporal。
- 模板 templates/module-template 完整可复制但不默认启用，用无领域 example.echo（message -> echo）验证注册无需修改核心。

## 前端功能

左栏：使用说明/固定页面/统一对话/运行与记录/模型配置/模块契约。登录表单默认显示可用演示账号说明。固定页面与对话复用 CapabilityForm（按input_schema生成）、ResultView、ResourceCard、ModelSettings 组件。模型配置可以由对话内工具栏打开相同组件。所有能力从registry读取，不为text模块硬编码路由。界面需展示离线规则与真实模型模式区别、任务失败/等待/重试、密钥仅已配置标识。

根代理负责 app/core backend、auth、registry、invoke、conversation、runs、resources、artifacts、start scripts、整体验收。前端agent只写 web/。模型agent只写 app/model_gateway.py、app/model_routes.py 和对应模型测试。模块agent只写 extensions/、templates/、docs/module-contract.md 和模块测试。所有agent需要通知接口变更，互不覆盖。
