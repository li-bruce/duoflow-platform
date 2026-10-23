# 模块开发步骤

## 1. 准备环境

按仓库 README 建立 Python 3.12 环境，安装锁定依赖和本仓库 TypeScript。Node 流程使用 Node 24。所有测试记录和模块包保存在独立目录。

## 2. 业务建模

生成基础模板后，以该模块目录为共同工作根。通过 business-observer 明确业务对象、动作、职责、范围、状态与结果，维护来源和独立预期。业务实现开始前解决实际影响当前行为的关键歧义，后续变更更新同一张图。

## 3. 完成业务和界面

Python 工具在 backend/business.py 实现业务，backend/handlers.py 接入上下文。Node 流程的 backend/business.mjs 保存对象、状态与事务；backend/server.mjs 装配 SDK 和真实函数表。frontend/Component.tsx 或 steps.tsx 同时服务页面与对话。SDK 不替代业务对象权限与数据范围检查。

公开动作应有稳定编号和版本。创建、更新、查询与正式状态检查分别明确；重试沿用业务请求身份，数据更新检查 expected_revision。长任务完成通过真实状态确认。注册声明中的业务步骤只是公开描述，当前平台仍需独立建设通用组合执行器。

## 4. 连续维护和测试

按 docs/OBSERVER.md 安装支持文件并维护映射。每轮先运行 `change_sync begin`，完成修改后运行 `bo_sync scan`，再依次运行 `change_sync inspect`、`change_sync verify`、`change_sync accept` 和 `change_sync check`。扫描与变更检查是两个独立工具，具体参数以固定版本的上游说明为准。正常与拒绝场景独立定义，实际读存储和函数结果；关闭或故障观测时核对业务结果。

从套件根运行 `python scripts/module_kit.py check <模块目录>`。Python 模板运行 `python <模块目录>/tests/self_test.py`；Node 模板在自身根运行 `node --test tests/workflow.test.mjs`。这些合成测试与平台的真实授权联调分别记录。

## 5. 主平台接入

按 [平台说明](https://github.com/li-bruce/duoflow-platform/blob/v0.01/README.md) 启动本次配套平台。由授权维护者安装共享组件和流程，注册权限定义并配置服务连接。发现、验收、发布分开执行；七项全部通过后发布，再给予真实用户适当业务授权。

用同一正式对象完成固定页面、对话续办、提交、反馈、修改、再提交和完成，核对刷新与分支。记录平台版本、套件版本、模块版本、Observer 提交、实际检查范围和未完成项。
