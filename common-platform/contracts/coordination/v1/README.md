# 业务状态协同 1.0

每个模块独立维护正式业务状态。平台的独立协同中心收集已提交的状态事件，按项目和机构汇总，再向明确订阅的模块分发消息。技术监控继续负责进程健康、心跳和负载；业务协同负责对象状态、公开审批和更新通知。

## 注册声明

远程模块可在 `registration.json` 增加 `coordination`。没有该字段时按原方式接入。空声明可作为开发入口：

```json
{"version":"1.0","machines":[],"emits":[],"accepts":[]}
```

完整例子（模块 `example.intake` 已声明数据对象 `example.intake.batch` 及对应业务状态机）：

```json
{
  "version": "1.0",
  "machines": [{
    "id": "example.intake.batch-lifecycle",
    "object_types": ["example.intake.batch"],
    "states": ["draft", "pending", "published", "withdrawn"],
    "public_states": ["published"],
    "approval_required": true
  }],
  "emits": ["state.changed", "publication.requested", "resource.published", "resource.withdrawn"],
  "accepts": [{"source_module": "example.governance", "kind": "resource.published", "mode": "sync"}]
}
```

`machines` 最多 32 项；机器编号最多 160 个字符，属于本模块命名空间，`object_types` 引用本模块已注册的数据对象。每台机器最多声明 100 个状态，每个状态编号最多 128 个字符，`public_states` 是其中的公开状态。registration 1.1 下，机器编号和完整状态集合与 `module_graph.state_machines` 中的业务状态机一致；1.0 使用本声明描述状态。机器列表可只选择需要参与协同的业务状态机。

`emits` 列出模块能发出的四类业务事件，最多四项。`accepts` 最多 128 项，明确填写外部来源模块、事件种类以及 `notify`（通知）或 `sync`（同步）方式；同一来源和事件只选一种方式。来源必须是具体模块编号。外部可订阅 `state.changed`、`resource.published`、`resource.withdrawn`；`publication.requested` 进入中心私人审批列表。公开审批的 `publication.approved`、`publication.rejected` 是中心向申请模块的处理回执，随审批请求定向投递。

模块接入时平台与 Node/Python 模块 SDK 都校验声明；握手要求平台声明支持 `coordination-1.0`。声明表明模块支持哪些能力；实际订阅由管理员选择来源、接收模块、机构与项目后启用。声明本身不创建订阅。填写声明后，模块还需要在正式状态事务中写入 outbox，并实现接收消息的业务处理函数，提交后再确认投递。Python 原生简单工具的现有 local 模板不使用远程 `registration.json`；独立 Python 微服务可使用同批 Python 注册与协同 SDK 接入，再按已交付对象和状态机填充声明。

## 平台怎样协同

例如汇交团队处理批次 A，智能挖掘同时查询其他已公开批次。批次 A 提交公开申请后，中心把申请保存在审批列表。管理员同意后，中心将决定投递给汇交模块；汇交模块再次核对批次与修订，在自己的事务内变成 `published` 并写入 `resource.published` 事件。中心接收后，按已配置的机构与项目向挖掘、治理和研究模块投递。挖掘模块可更新自己的索引，研究模块可生成自己的更新通知，它们继续各自的办理流程。

中心只传递对象引用、状态和修订，业务正文由接收模块通过公开接口按当前权限读取。每个模块使用自身事务 outbox 发布已提交事件，使用 inbox 记录已处理投递；中心以租约重试和对象内顺序保证离线后补收，同一对象有序、不同对象可并发。模块的正式状态仍由它自己的业务函数维护。

声明精确字段与数组上限见 [declaration.schema.json](declaration.schema.json)。平台纯校验入口为 `app/coordination_contract.py::validate_coordination`，供离线 Kit 与平台接入共同使用。事件、审批与投递的 HTTP 契约见同批中心实现及其 API 文档。
