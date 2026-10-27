# 版本对应

仓库发布标签 **v0.02**，对应 DuoFlow Platform **v0.02**。npm 工具元数据采用 `0.0.2`，平台既有内部组件各自保留版本；仓库标签与协议版本分开维护，v0.01 历史标签保留。

| 内容 | 对应版本 |
| --- | --- |
| 模板元数据 kit_version | 1.1.0；兼容旧 1.0.0 包 |
| 远程 manifest / invoke | 1.2 / 1.1 |
| registration | 新模块 1.1；旧服务保留 1.0 |
| access / workflow / interaction | 1.0 |
| flow_exports / runtime_observation | 1.0 / 1.0 |
| composition | 1.0；平台提供顺序执行，套件提供公开契约 |
| module_graph / Observer 映射 | 1.0 / 2（旧包仍可使用映射 1） |
| business-observer | business-observer.lock.json 的精确提交 |

本地未发布适配候选新增可选 `source-reads-1.0` 特征和 source_read_version 1.0。
依赖它的模块通过 SDK 的 requiredPlatformFeatures / required_platform_features 要求握手确认；
未声明依赖的旧模块保持原有接入。源读取错误仅携带安全 code 和 HTTP status，不将契约错误统一当成缺少权限。
前端检查返回可携带 frontend_files 闭包，Observer preflight 仅作结构诊断；完整业务、授权和 Observer gate 仍分别验证。

本地后续候选新增可选 `ai-services-1.0` 与 `registration.ai_services`。声明AI的模块使用同批Python/Node AIServiceManager、模块私有加密配置和配置控制接口；接入后按平台显式保存的来源选择平台broker或模块自身API，无自动回退。旧无AI声明模块保持兼容。协议详见 `contracts/ai/v1/`，操作与限制见[模块AI服务与配置](docs/模块AI服务与配置.md)。该候选尚未改写已发布v0.02标签。

`contracts/` 随本仓库提供，静态校验器来自平台相同实现，来源散列见 VALIDATOR-SOURCES.json。升级时统一复核协议、SDK、模板、映射和负例测试；不要独立刷新散列来掩盖接口差异。目标平台完成七项验收才可发布，离线检查无法确认实际安装和真实授权。

Python、JS/TS、React、Vue 源码映射使用固定 Observer 的相应适配器；其解析依赖需要独立安装。状态与动态调用仍需实际采集。原生 bo.fsm/1 是平面子集；完整层级/并行业务模型保留在模块中，平台状态图描述当前协议支持的公开部分。套件使用 Observer 对应的 jsonschema 4.25.1，平台实现使用其锁定版本；两侧公共 Schema 和行为通过配套测试核对。
