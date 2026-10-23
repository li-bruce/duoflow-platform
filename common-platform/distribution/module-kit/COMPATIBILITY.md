# 版本对应

仓库发布标签 **v0.01**，对应 DuoFlow Platform **v0.01**。npm 工具元数据采用 `0.0.1`，平台既有内部组件各自保留版本；仓库标签与协议版本分开维护。

| 内容 | 对应版本 |
| --- | --- |
| 模板元数据 kit_version | 1.0.0 |
| 远程 manifest / invoke | 1.2 / 1.1 |
| registration / access / workflow / interaction | 1.0 |
| business-observer | business-observer.lock.json 的精确提交 |

`contracts/` 随本仓库提供，静态校验器来自平台相同实现，来源散列见 VALIDATOR-SOURCES.json。升级时统一复核协议、SDK、模板、映射和负例测试；不要独立刷新散列来掩盖接口差异。目标平台完成七项验收才可发布，离线检查无法确认实际安装和真实授权。

Python、JS/TS、React、Vue 源码映射使用固定 Observer 的相应适配器；其解析依赖需要独立安装。状态与动态调用仍需实际采集。原生 bo.fsm/1 是平面子集；完整层级/并行业务模型保留在模块中，平台状态图描述当前协议支持的公开部分。套件使用 Observer 对应的 jsonschema 4.25.1，平台实现使用其锁定版本；两侧公共 Schema 和行为通过配套测试核对。
