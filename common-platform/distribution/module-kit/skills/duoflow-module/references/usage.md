# 套件和业务建模依赖

本 Skill 的配套仓库是 [duoflow-module-kit v0.01](https://github.com/li-bruce/duoflow-module-kit/tree/v0.01)。使用者可以提供已检出的仓库位置；在任务允许的工作目录获取仓库后，按 README 安装其锁定依赖。生成器、SDK、模板和校验器来自该仓库，不能仅凭 Skill 文本模拟其结果。

- [模块开发步骤](https://github.com/li-bruce/duoflow-module-kit/blob/v0.01/docs/DEVELOPMENT.md)
- [Observer 接线和联合检查](https://github.com/li-bruce/duoflow-module-kit/blob/v0.01/docs/OBSERVER.md)
- [功能和支持范围](https://github.com/li-bruce/duoflow-module-kit/blob/v0.01/docs/FEATURES.md)
- [版本对应](https://github.com/li-bruce/duoflow-module-kit/blob/v0.01/COMPATIBILITY.md)

从实际套件根运行脚本，模块目录单独指定。执行前核对 VERSION 和 business-observer.lock.json。首次采用按固定上游要求确认执行方式与业务含义；沿用已明确的同范围决定，记录变更来源。所有实际图、源码绑定和检查结果放在业务项目中。
