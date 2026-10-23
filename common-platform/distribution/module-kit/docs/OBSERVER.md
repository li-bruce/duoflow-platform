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

每个公开能力都映射到真实模型节点。流程模块在 states 中增加 `{"example.review.workflow":{"draft":"record.draft","submitted":"record.submitted","approved":"record.approved"}}`，使用实际流程/状态/节点编号；被引用节点 kind 为 state。该示例仅展示结构，不能当成已确认业务或已运行证据。

完整内部业务图可包含层级、并行、等待和资源；公开 state_machine 为当前平台支持的视图，两者通过明确映射关联。无法表达的语义保留并报告，不静默丢失。

## 联合检查

```powershell
python scripts/observer_bridge.py mapping --module work/example-text
python scripts/observer_bridge.py gate --source work/observer --module work/example-text
```

mapping 检查文件、公开能力和状态关联；gate 先核验上游来源，再运行模块检查、需求/检查关联、change_sync 和 architecture_guard。缺少模型、同步配置、实际证据或上游检查失败时，gate 返回失败。错误只输出稳定分类，详细业务调试在模块自身检查中进行。

持续开发按上游 SKILL.md 执行 begin → scan → inspect → verify → accept → check；已有活跃变更继续原基线。将本映射、生成器、Skill 和检查配置也纳入项目维护范围。源码移动保留节点身份并提供显式迁移。

源码扫描使用上游 Python、JS/TS、React、Vue 适配器。Node 模块须提供显式状态/调用采集和独立用例；静态候选不代表实际调用。观测记录与页面不参与正式业务裁决；失效、关闭和丢失采集场景分别验证。
