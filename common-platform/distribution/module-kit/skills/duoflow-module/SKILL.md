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

1. 从仓库根运行 `scripts/module_kit.py scaffold --help`。一次处理得到结果采用 tool-python；正式对象流程可从 workflow-node 开始。已有 Python 远程流程按适配说明接入，保留专业业务库。
2. 用 `observer_bridge.py init --module <模块> --source <Observer仓库>` 安装固定版本的状态查询、观测和图组件。按 Observer 建立 discovery.json、intent.json、behavior.json、sync.json、独立业务预期、探针和架构归属。复制支持文件不代表业务模型已经完成。
3. 明确能力输入输出、正式对象、后端状态与事务、实际权限及范围。使用套件 SDK。固定页面和对话引用相同组件与业务对象；个人草稿、正式状态、一次调用状态分别保存。
4. 将能力和公开状态绑定到业务模型稳定节点，按 docs/OBSERVER.md 维护 duoflow-observer.json。模块内部完整图与平台公开状态图分别保留引用；无法表达的并行或等待语义显式报告，不能直接压平。
5. 每轮按 Observer 的 `change_sync begin` → 修改业务 → `bo_sync scan` → `change_sync inspect` → `change_sync verify` → `change_sync accept` → `change_sync check` 更新受影响材料。accept 只在真实检查通过后执行。保持原业务状态裁决者，观察器只读取实际事实。Python、JS/TS、React、Vue 使用固定版本对应的源码适配器；动态回调与状态需要实际采集。原生 bo.fsm/1 仅支持平面子集，复杂并发使用已有执行器的明确适配。
6. 执行模块静态检查、模板独立测试、observer_bridge gate，再到已授权平台完成安装、真实授权、双向验收和发布。逐步验证页面创建、同事项对话续办、提交、反馈、修改、再提交、完成、刷新及分支，记录真实对象和结果。

## 完成标准

交付模块源码、清晰目录说明、主要函数注释、公开协议、业务模型、映射和验证入口。分别报告结构/映射检查、业务行为、观测隔离、双端办理和平台注册结果；未执行项保持未验证。主框架的通用跨模块自动执行尚待建设，模块 Skill 只能实现实际可用协议支持的内容。

详细要求见 [配套仓库与使用入口](references/usage.md)。随仓库使用时可直接阅读 docs；单独安装 Skill 时仍须定位配套套件和固定 Observer 来源。
