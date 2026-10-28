# 模块开发步骤

## 1. 准备环境

按仓库 README 建立 Python 3.12 环境，安装锁定依赖和本仓库 TypeScript。Node 流程使用 Node 24。所有测试记录和模块包保存在独立目录。

## 2. 业务建模

生成基础模板后，以该模块目录为共同工作根。通过 business-observer 明确业务对象、动作、职责、范围、状态与结果，维护来源和独立预期。业务实现开始前解决实际影响当前行为的关键歧义，后续变更更新同一张图。

## 3. 完成业务和界面

Python 工具在 backend/business.py 实现业务，backend/handlers.py 接入上下文。Node 流程的 backend/business.mjs 保存对象、状态与事务；backend/server.mjs 装配 SDK 和真实函数表。frontend/Component.tsx 或 steps.tsx 同时服务页面与对话。SDK 不替代业务对象权限与数据范围检查。

公开动作应有稳定编号和版本。创建、更新、查询与正式状态检查分别明确；重试沿用业务请求身份，数据更新检查 expected_revision。长任务完成通过真实状态确认。

在 `backend/module-graph.json` 登记所有前后端原子、输入输出、事件、状态和业务节点关系。在 `backend/flow-exports.json` 命名公开的功能与流程；部分流程通过实际入口及 `completed_states` 明确边界。保持注册附件与这些文件一致。平台 composition 1.0 可连接这些入口，模块仍维护自己的正式状态和权限判断。

## 4. 连续维护和测试

实际使用AI的能力还需按[模块AI服务与配置](模块AI服务与配置.md)在 `registration.ai_services` 声明稳定服务、关联能力和支持的连接来源，接入同批AIServiceManager与配置控制路由。模块自己的地址/模型/密钥存入私有配置；平台管理界面可代理配置并显式选择平台API或模块自有API。业务传真实调用scope，使用返回的route在最终写入前复验；测试两种来源、停用、错误、切换、当前撤权和保存后重启，不用环境变量捷径绕过中央来源绑定。没有AI行为的模块无需添加声明。

安装前使用 `check` 返回的 `frontend_files` 生产依赖清单，保持包内相对结构。源仓库中的预览/测试文件与包外样例不自动安装。可用 `scripts/module_kit.py check <模块> --frontend-only` 独立核对这部分；它只调用静态 TypeScript 解析，不导入后端权限引擎。完整 `check` 仍要求同批 Kit 的纯校验依赖，且不代替真实宿主构建。

按 docs/OBSERVER.md 安装支持文件并维护映射。每轮先运行 `change_sync begin`，完成修改后运行 `bo_sync scan`，再依次运行 `change_sync inspect`、`change_sync verify`、`change_sync accept` 和 `change_sync check`。扫描与变更检查是两个独立工具，具体参数以固定版本的上游说明为准。正常与拒绝场景独立定义，实际读存储和函数结果；关闭或故障观测时核对业务结果。

从套件根运行 `python scripts/module_kit.py check <模块目录>`。Python 模板运行 `python <模块目录>/tests/self_test.py`；Node 模板在自身根运行 `node --test tests/workflow.test.mjs`。这些合成测试与平台的真实授权联调分别记录。

## 5. 主平台接入

需要跨模块固定来源时，检查配套平台是否提供可选 `source-reads-1.0`。同批 SDK 的 `source-read-client.mjs` / `source_reads.py` 调用固定平台回调，由平台重新检查两端并通过已登记 `objects.read` 读取。目标票据不转递到源，源票据不进入表单/历史。详见随包 `contracts/access/v1/source-reads.md`；缺少该能力时明确拒绝来源读取，不能信任客户端自报数据替代授权。

Python SDK 可在套件自己的环境中运行 `python -m pytest templates/module-kit/sdk/python/tests -q --basetemp work/sdk-tests`。锁文件包含实际 HTTP 客户端依赖；需要主平台的授权集成用例归属主框架测试目录，套件测试仅使用本仓库源码。

按 [平台说明](https://github.com/li-bruce/duoflow-platform/blob/v0.02/README.md) 启动本次配套平台。由授权维护者安装共享组件和流程，注册权限定义并配置服务连接。发现、验收、发布分开执行；全部检查通过后发布，再给予真实用户适当业务授权。新模块使用 1.1 握手，主动回调平台并确认实际挑战已被接收。

用同一正式对象完成固定页面、对话续办、提交、反馈、修改、再提交和完成，核对刷新与分支。记录平台版本、套件版本、模块版本、Observer 提交、实际检查范围和未完成项。
