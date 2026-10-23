# 版本对应

仓库标签 **v0.01**，配套模块套件 **v0.01**。现有内部平台实现版本为 0.4.0；v0.01 是本次独立双仓库的发布版本，二者含义分别记录。

协议继续采用 manifest 1.2 / invoke 1.1；registration、access、workflow、interaction 为 1.0。旧协议兼容范围见公共契约与模块说明。升级模块套件须同时复核公开声明、SDK、共享组件和平台验收。

根 RELEASE-MANIFEST.json 是本次发布内容散列。EXPORT-MANIFEST.json 是可携带导出基线，provenance/IMPORT-MANIFEST.json 保留最初导入证明；后续修改不覆盖历史证明。散列只说明文件身份，业务与运行验证另行记录。
