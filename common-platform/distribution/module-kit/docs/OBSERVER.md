# business-observer 接线与同步

上游：[SoftwareStatusObserverSkill](https://github.com/li-bruce/SoftwareStatusObserverSkill)。本版使用 business-observer.lock.json 中的固定提交；该文件包含仓库地址、提交和规范化 UTF-8 文件散列。升级由维护者审查后修改锁和回归验证。

## 获取和验证

```powershell
git clone https://github.com/li-bruce/SoftwareStatusObserverSkill.git work/observer
# 查看 business-observer.lock.json 的 commit，在上游副本 checkout 此精确提交
git -C work/observer checkout <commit>
.\.venv\Scripts\python.exe scripts/observer_bridge.py source --source work/observer
.\.venv\Scripts\python.exe scripts/observer_bridge.py init --source work/observer --module work/example-text
```

init 调用上游 bo_upgrade，安装 bo_stategraph、observer_runtime 和 static/graph。它保留本地修改冲突，尚未编写业务模型时会明确显示待建模。该命令不生成或伪造业务事件。

固定上游要求先明确 drive 或 observe，并记录用户回答；业务实现前展示局部图与实际可打开的 Web 地址并确认当前业务含义。新项目使用 bo_stategraph/project.json（bo-project/v1）作为共同规范源，维护选择及内嵌 model。原生执行器与探针按上游多语言接线说明配置。JS/TS/React/Vue 分析另运行 `npm ci --prefix work/observer/skills/business-observer/scripts/bo_observer/adapters/javascript` 安装固定解析器。Python 共享服务依赖已包含在本套件锁定依赖中。

## 交接材料

业务根保存 discovery.json、intent.json、behavior.json、acceptance.json，以及 Observer 所需 sync.json、独立预期、源码映射和架构归属。文件名可适配，引用须留在模块目录内。业务源码和状态以模块实际存储为准。

新项目在映射中额外填写 `"project":"bo_stategraph/project.json"`。behavior.json 由该文件内的 model 生成，检查器要求二者一致，联合 gate 还调用上游 bo.py validate。既有 v3 项目可以保留当前模型文件布局；两份定义不能各自修改。

duoflow-observer.json 示例：

```json
{
  "version": 1,
  "discovery": "discovery.json",
  "intent": "intent.json",
  "model": "behavior.json",
  "acceptance": "acceptance.json",
  "capabilities": {"example.text.use": ["text.process"]},
  "states": {}
}
```

以上版本 1 用于旧模块。带 module_graph 契约的新模块使用版本 2，并增加以下三项：`module_graph` 指向 `backend/module-graph.json`；`nodes` 将每个公开节点 ID 对应到完整模型节点 ID；`atoms` 将每个原子 ID 对应到其模型节点列表。atoms 必须与公开图的 mappings 一致，后端原子的节点也必须与 capabilities 映射一致。正式状态仍通过 states 绑定。

将 module-graph.json 列入 sync.json 的 watch_files 或模型 source_files，确保修改登记会使旧同步凭据失效。不能只维护能力列表而漏掉前端交互或状态节点。映射检查是结构对应检查，组件单元测试、真实业务读写与观察隔离另行验证。

每个公开能力都映射到真实模型节点。流程模块在 states 中增加 `{"example.review.workflow":{"draft":"record.draft","submitted":"record.submitted","approved":"record.approved"}}`，使用实际流程/状态/节点编号；被引用节点 kind 为 state。该示例仅展示结构，不能当成已确认业务或已运行证据。

完整内部业务图可包含层级、并行、等待和资源；公开 state_machine 为当前平台支持的视图，两者通过明确映射关联。无法表达的语义保留并报告，不静默丢失。

## 联合检查

开始第一轮业务实现前，把以下内容落到实际文件中。完整 gate 会逐项检查这些材料：

| 材料 | 应包含的实际内容 |
| --- | --- |
| sync.json 的 source_scope | roots 使用项目根 `.`；逐项写明测试、固定 SDK 和观察器支持文件的排除理由，固定副本保留散列 |
| 模型的 implementation | `bo-implementation/v1` 功能清单：入口函数、前端事件回调、辅助函数归属、参数的安全采集方式和真实场景 |
| discovery.json | 每个业务节点的 model_nodes 指向实际模型节点；公开组件与完整内部模型同时维护 |
| observations_command | 指向已经存在、纳入 watch_files 的脚本；脚本执行真实调用，独立读取存储，并比较公开投影 |
| ownership.json | 业务、服务装配、界面、协议、SDK、观察支持和测试的目录归属及允许的依赖方向 |

检查脚本需要实际执行开启、关闭和故障三种观察条件。后端验证返回结果及数据库中的状态和修订；前端验证组件触发的宿主操作、草稿变化和交互结果。列表观察要说明记录范围，不能用一个节点代表未执行的全部列表分支。

状态事件在真实结果返回或事务完成后采集。动态封装可在服务装配时用稳定节点和阶段绑定探针，再在真实回调中记录；声明的静态位置应与绑定位置一致。新增源码后重新运行源码清单和 enrich，检查所有入口、参数和捕获字段都有归属及实际场景。

既有活跃变更继续原基线；先补齐缺项，再 inspect、verify、accept 和 check。失败记录保留。接受后补充架构或其他受检查文件时，另开一轮变更。联合检查的通过记录、原子单元测试和真实平台接入分别保存。

```powershell
python scripts/observer_bridge.py mapping --module work/example-text
python scripts/observer_bridge.py gate --source work/observer --module work/example-text
```

mapping 检查文件、公开能力和状态关联；gate 先核验上游来源和同步材料，再运行模块检查、需求/检查关联、change_sync 和 architecture_guard。缺少模型、同步配置、实际证据或上游检查失败时，gate 返回失败。错误只输出稳定分类，详细业务调试在模块自身检查中进行。

映射版本 2 还填写 `flow_exports: "backend/flow-exports.json"` 和 `flows`。flows 的键是每个公开业务流 ID，值为其 node_ids 对应的完整业务模型节点列表；应与 nodes 翻译关系一致。同步配置必须同时跟踪 module-graph.json 与 flow-exports.json。输入输出、权限或入口边界改变时，更新公开声明、Observer 映射、真实场景预期和接入验收。

同步材料必须对应同一组文件：sync.json 的 model 指向映射中的 model，或已声明的 project；acceptance 和 discovery 指向映射中的对应文件；模型内的 intent 指向映射中的 intent。使用 project 时，其内嵌 model 必须与生成的模型相同。路径可采用等价写法，但另一份内容相同的文件也需要重新绑定和验收，避免将其他材料的通过记录用于当前模块。

持续开发按上游 SKILL.md 执行 `change_sync begin` → 修改业务 → `bo_sync scan` → `change_sync inspect` → `change_sync verify` → `change_sync accept` → `change_sync check`；已有活跃变更继续原基线。扫描入口是 bo_sync，其余变更检查由 change_sync 执行。将本映射、生成器、Skill 和检查配置也纳入项目维护范围。源码移动保留节点身份并提供显式迁移。

源码扫描使用上游 Python、JS/TS、React、Vue 适配器。Node 模块须提供显式状态/调用采集和独立用例；静态候选不代表实际调用。观测记录与页面不参与正式业务裁决；失效、关闭和丢失采集场景分别验证。
