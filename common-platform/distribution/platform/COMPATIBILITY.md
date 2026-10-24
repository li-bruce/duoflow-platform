# 版本对应

仓库标签 **v0.02**，配套模块套件 **v0.02**。既有内部平台实现版本为 0.4.0；仓库发布标签和接口版本分别维护，v0.01 历史标签保留。

协议继续采用 manifest 1.2 / invoke 1.1；registration 新模块使用 1.1 并保留 1.0 兼容。access、workflow、interaction、module_graph、flow_exports、runtime_observation 和 composition 均为 1.0。模块模板 kit_version 为 1.1.0，Observer 原子映射版本为 2。升级模块套件须同时复核公开声明、SDK、共享组件和平台验收。

根 RELEASE-MANIFEST.json 是本次发布内容散列。EXPORT-MANIFEST.json 是可携带导出基线，provenance/IMPORT-MANIFEST.json 保留最初导入证明；后续修改不覆盖历史证明。散列只说明文件身份，业务与运行验证另行记录。
