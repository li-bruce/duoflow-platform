# 从哪里阅读源码

先读 `scripts/module_kit.py:scaffold/check`，再按业务类型进入模板，最后查看 SDK 与验证器。

| 目录 | 职责 | 阅读重点 |
| --- | --- | --- |
| contracts/ | 独立公开协议和 Schema | manifest、invoke、access、registration、workflow、module-graph、flow-exports、runtime-observation、compositions |
| scripts/module_kit.py | 生成包、解析源码及检查引用 | scaffold、frontend_exports、check |
| duoflow_validation/ | 平台纯校验逻辑的发布副本 | register、validate_definition、validate_registration |
| templates/module-kit/blueprints/ | 两种可运行模板 | business、handlers/server、共享前端 |
| templates/module-kit/sdk/ | Python/Node 可携带 SDK | 身份授权边界、真实函数表、请求与回执 |
| scripts/observer_bridge.py | 固定 Observer 来源、支持文件和联合检查 | source_check、init、check_mapping、check_sync_inputs、gate |
| skills/duoflow-module/ | 开发工作流与材料入口 | 业务确认、实现、持续同步和交付 |
| tests/ | 独立生成与错误声明回归 | 缺失映射、未知引用、观察器缺项等负例 |
| docs/ | 功能、用法与产品介绍 | index.html 与 demo.html 分开 |

生成器采用新目录写入和校验后改名，已有目录保留。`check` 只读取文件和解析 AST，不导入供应商业务。Python SDK 对接可信上下文；Node SDK 对接固定接口和中央授权。正式业务状态与事务留在业务服务。

AI服务的可选声明在 `backend/registration.json` 的 `ai_services`。`sdk/node/ai-service.mjs`、`sdk/python/ai_service.py` 管理模块自有加密配置和实际调用；接入后的来源绑定归主框架，模块传当前票据和可信范围事实，平台Key不下发。`chatWithReceipt` / `chat_with_receipt` 返回结果与无密钥路由引用，业务提交前再次 `validate`。这提供前后复验，不是跨平台/模块的分布式原子提交。见[完整说明](模块AI服务与配置.md)。

验证器由平台真实函数通过 AST 选取，移除数据库和服务装配依赖；原函数体保留，来源见 VALIDATOR-SOURCES.json。主平台同步测试检查抽取逻辑及拒绝行为。它是发布副本，升级需来自配套平台版本。

新增动作应同时检查公开声明、实际处理器、权限映射、输入输出、共享组件和业务模型；新增状态还需检查后端转移、状态图、数据版本与独立业务预期。注释说明目的、输入来源和失效条件，避免只复述函数名称。

## 从组件到整体业务

`module-graph.json` 保存组件定义与业务节点关系；运行实例使用独立实例编号关联实际对象。前端事件对应共享宿主支持的操作，后端原子对应真实能力处理器。`flow-exports.json` 从完整模块中选出对外入口、数据权限和完成边界；主平台按这些公开定义构建组合。

`sdk/python/graph_testing.py` 与 `sdk/node/graph-testing.mjs` 核对真实动作前后的对象和状态，校验原子结果。运行事件在实际处理后发出；发送失败保留业务结果，按有界队列补送，平台显示缺失与过期情况。完整业务模型和源码关联由 Observer 持续维护，公开图用于平台连接与观察。

## 独立业务协同中心

协同服务接收模块业务事件，维护对象最新状态、公开审批和按订阅分发的消息。状态、版本和对象归模块；中心把多个并行业务连接起来。模块在自身事务内写Outbox，收到消息后把处理和Inbox同事务提交，再确认投递。Node/Python SDK与公开申请示例随套件提供，完整接线和生命周期见 docs/并行业务状态协同.md。
