---
name: duoflow-module
description: 为 DuoFlow 新建或改造业务模块，使用 business-observer 先明确业务网和状态，再实现协议、权限、Python/Node 服务及页面与对话共用组件，持续维护映射并验证接入。
---

# DuoFlow 模块构建

用于新模块和既有服务接入。运行工具前定位本 Skill 所在仓库根，读取该仓库 README、COMPATIBILITY.md 和 docs/DEVELOPMENT.md；业务代码遵循目标项目约定。

## 业务定义和模型

使用 business-observer 的业务建模与持续开发流程。来源和固定提交见仓库根 business-observer.lock.json，安装方式见 docs/OBSERVER.md。先执行 `scripts/observer_bridge.py source --source <Observer仓库>` 核对实际源码。缺少依赖时可继续梳理已知要求并说明缺项，不伪造已完成建模或验证，也不擅自安装到用户全局目录。

先建立模板基础目录，再将业务讨论记录、图和验收预期保存在同一模块根。业务实现以已确认的对象、动作、职责、范围、状态和结果为依据。复用用户已有回答；只追问会改变当前业务含义的歧义。未确认规则保持待确认。调用 Observer 时读取其 SKILL.md 和本次所需参考资料。

按固定上游版本，首次采用时确认 drive（从状态声明执行）或 observe（保持应用逻辑并观测），记录用户原话和定位；已有明确选择直接沿用。回答前可准备环境、模板和协议。业务实现前用相同 discovery 和稳定 ID 给出局部图、已验证 Web 地址及待定项，复用相同范围的已有确认。不要把模板默认执行方式或浏览记录当作用户选择。

## 实现与接入

首次 begin 前执行 `observer_bridge.py preflight --module <模块>` 检查映射计划/已有基线结构；这不等于 runtime coverage 或同步通过。若固定上游曾接受不完整初稿，保留原基线和失败，按稳定诊断报告，禁止通过重置或修改受保护基线隐藏问题。完整联合 gate 仍须单独通过。

1. 从仓库根运行 `scripts/module_kit.py scaffold --help`。一次处理得到结果采用 tool-python；正式对象流程可从 workflow-node 开始。已有 Python 远程流程按适配说明接入，保留专业业务库。
2. 用 `observer_bridge.py init --module <模块> --source <Observer仓库>` 安装固定版本的状态查询、观测和图组件。按 Observer 建立 discovery.json、intent.json、behavior.json、sync.json、独立业务预期、探针和架构归属。复制支持文件不代表业务模型已经完成。
   第一轮 begin 前核对 docs/OBSERVER.md 的材料表：扫描项目根、bo-implementation/v1 功能与参数清单、discovery.model_nodes、实际存在且被跟踪的 observations_command、ownership.json。前端 JSX 事件入口也要登记；动态探针绑定给出真实静态位置和实际执行场景。后端以独立存储快照核验状态，前端以实际组件操作核验宿主效果，观察开关和故障保持业务结果一致。
3. 明确能力输入输出、正式对象、后端状态与事务、实际权限及范围。填写 backend/module-graph.json，逐个登记前后端原子、输入输出、事件、交互效果、局部状态及业务节点映射。后端 Schema 取 manifest，前端事件与实际 host 一致；组件定义、实例、业务节点和调用分别标识。使用套件 SDK 对照实际函数表。固定页面和对话引用相同组件与业务对象；个人草稿、正式状态、一次调用状态分别保存。
4. 将能力和公开状态绑定到业务模型稳定节点，按 docs/OBSERVER.md 维护 duoflow-observer.json。新原子登记使用映射版本 2，覆盖所有 atoms 与公开 nodes，并将 module-graph.json 纳入同步检查。模块内部完整图与平台公开状态图分别保留引用；无法表达的并行或等待语义显式报告，不能直接压平。
   在 backend/flow-exports.json 声明多个公开业务入口。入口引用实际 capability 或 workflow，后者给出完成状态。部分业务单独命名并给出明确边界；数据操作引用注册对象，入口权限与实际接口保持一致。全部后续动作仍执行自身授权；完整协作流程允许不同人员分别办理。
5. 每轮按 Observer 的 `change_sync begin` → 修改业务 → `bo_sync scan` → `change_sync inspect` → `change_sync verify` → `change_sync accept` → `change_sync check` 更新受影响材料。accept 只在真实检查通过后执行。保持原业务状态裁决者，观察器只读取实际事实。Python、JS/TS、React、Vue 使用固定版本对应的源码适配器；动态回调与状态需要实际采集。原生 bo.fsm/1 仅支持平面子集，复杂并发使用已有执行器的明确适配。
6. 执行模块静态检查、模板独立测试、observer_bridge gate，再到已授权平台完成安装、真实授权、双向验收和发布。逐步验证页面创建、同事项对话续办、提交、反馈、修改、再提交、完成、刷新及分支，记录真实对象和结果。
   新远程模块使用 registration 1.1，完整附件与 module-graph.json、flow-exports.json 保持一致。握手主动回调平台运行同步接口，通过实际挑战后才确认 runtime_callback。后端在实际处理完成或提交后报告状态；前端观察只反映界面状态。分别测试观察接口失效、乱序、重复、当前撤权和对象撤权，观察故障不改变业务结果。

## 完成标准

业务使用AI时，在 `registration.ai_services` 声明实际服务及关联能力，按 `docs/模块AI服务与配置.md` 和 `contracts/ai/v1/` 使用同批Python/Node AIServiceManager。模块独立运行可配置自有API；接入后由平台管理员逐服务显式选择平台或模块连接，不静默fallback。密钥不进入声明/前端/业务回执；用可信scope调用，并在最终提交前复验返回的route、当前权限与配置修订。分别验证两个来源和切换，保持模型候选的领域校验与原审核，未用AI的模块无需占位声明。

交付模块源码、清晰目录说明、主要函数注释、公开协议、业务模型、映射和验证入口。分别报告结构/映射检查、业务行为、观测隔离、双端办理和平台注册结果；未执行项保持未验证。主框架 composition 1.0 支持顺序组合、明确字段传递、人工办理等待和实际运行图。模块提供自身入口、完成条件和可重新授权的对象读取接口；循环、通用条件分支和自动补偿保持未实现。组合场景应实测来源授权、目标权限、版本变化和中断后的凭据恢复。

详细要求见 [配套仓库与使用入口](references/usage.md)。随仓库使用时可直接阅读 docs；单独安装 Skill 时仍须定位配套套件和固定 Observer 来源。
