# 通用模块模板 v1

两侧完整说明：[模块框架说明](../../docs/模块框架说明.md)、[主框架说明](../../docs/主框架说明.md)。远程模块新增 registration 1.0，提供公开内容声明和双向握手；平台七项检查全部通过后才能发布。新 Node SDK 使用真实 handlers / workflowHandlers 表，已有服务升级时须同步适配。

先阅读 [模块模板使用手册](../../docs/模块模板使用手册.md)。本目录是模板和可携带服务端 SDK；它不会自行安装、启动、授权或修改现有业务。

| 路径 | 用途 |
| --- | --- |
| `blueprints/tool-python` | Python 原生简单工具，纯业务函数与页面/对话共用组件 |
| `blueprints/workflow-node` | Node 24 远程流程参考，项目记录、职责、状态、修订及幂等 |
| `sdk/python/module_kit.py` | 原生工具的授权与输入输出边界；生成时另复制到 backend |
| `sdk/python/remote_access.py` | 已有 Python/FastAPI 独立服务的中央授权客户端 |
| `sdk/node` | Node 固定传输、中央授权与单进程 SQLite 回执参考 |

在平台根目录运行 `python scripts/module_kit.py scaffold --help` 或 `python scripts/module_kit.py check <包目录>`，使用平台 Python 环境。共享前端静态检查还使用宿主 Node 与已安装 TypeScript，只解析 AST，不执行供应商源码。

`workflow-python-remote` 是已有 Python 服务的可检查适配布局，不是第三个新建生成器。原业务库和专业算法可以保留。SDK 是受控、随包发布的小型封装，不能替代服务器仓储事实、业务状态机或正式授权；不动态下载代码，不成为另一套 RBAC。

模板本身是包含占位符的源码，请先生成新包。修改业务文件后，执行生成包自测、当前平台 check，再在隔离平台安装联调。真实平台注册、生产浏览器操作、模型专业效果和外部生产存储分别验收。
