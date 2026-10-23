# 从哪里阅读源码

先读 `scripts/module_kit.py:scaffold/check`，再按业务类型进入模板，最后查看 SDK 与验证器。

| 目录 | 职责 | 阅读重点 |
| --- | --- | --- |
| contracts/ | 独立公开协议和 Schema | manifest、invoke、access、registration、workflow |
| scripts/module_kit.py | 生成包、解析源码及检查引用 | scaffold、frontend_exports、check |
| duoflow_validation/ | 平台纯校验逻辑的发布副本 | register、validate_definition、validate_registration |
| templates/module-kit/blueprints/ | 两种可运行模板 | business、handlers/server、共享前端 |
| templates/module-kit/sdk/ | Python/Node 可携带 SDK | 身份授权边界、真实函数表、请求与回执 |
| scripts/observer_bridge.py | 固定 Observer 来源、支持文件和联合检查 | source_check、init、check_mapping、gate |
| skills/duoflow-module/ | 开发工作流与材料入口 | 业务确认、实现、持续同步和交付 |
| tests/ | 独立生成与错误声明回归 | 缺失映射、未知引用、观察器缺项等负例 |
| docs/ | 功能、用法与产品介绍 | index.html 与 demo.html 分开 |

生成器采用新目录写入和校验后改名，已有目录保留。`check` 只读取文件和解析 AST，不导入供应商业务。Python SDK 对接可信上下文；Node SDK 对接固定接口和中央授权。正式业务状态与事务留在业务服务。

验证器由平台真实函数通过 AST 选取，移除数据库和服务装配依赖；原函数体保留，来源见 VALIDATOR-SOURCES.json。主平台同步测试检查抽取逻辑及拒绝行为。它是发布副本，升级需来自配套平台版本。

新增动作应同时检查公开声明、实际处理器、权限映射、输入输出、共享组件和业务模型；新增状态还需检查后端转移、状态图、数据版本与独立业务预期。注释说明目的、输入来源和失效条件，避免只复述函数名称。
