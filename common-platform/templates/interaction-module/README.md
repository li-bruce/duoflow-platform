# 组件模块骨架

先阅读生成的 `prompts/组件开发提示词.md`。本目录是可复制骨架，不自动安装，不包含真实凭据。

- `component/`：两端共用 React 组件与注册定义；供应商的业务交互在这里。
- `backend-python/`：原生 Python 能力，可复制进 `extensions/<module>/` 并加入原有安装清单。
- `backend-node/`：manifest 1.2 / invoke 1.1 的独立 Node.js 服务；执行 `npm install --ignore-scripts`，通过私有环境配置 `PLATFORM_SERVICE_TOKEN`，再运行 `npm start`。在主框架服务接入页填写地址、服务凭据，发现后发布。Node 20 以上；平台不把 Python 处理器加载到 Node 中。

两种后端示例使用相同能力 ID，便于选择一种验证；同时安装时必须分配不同模块与能力 ID。将选中的能力 ID 和版本写入 `component/interaction.json` 的动作绑定。

组件目录复制为 `components/packs/<pack>/`，保留 `Component.tsx` 和 `interaction.json` 文件名；在 `components/installed.json` 的 `packs` 数组追加目录名，保留已有条目。平台 SDK 通过 `@platform/interaction-sdk` 导入。先重建前端，再重启后端。网页与对话都会挂载同一组件。

Python 模块代码使用平台的 Python 依赖环境。Node 模块独立管理自己的依赖与进程；前端组件在平台构建环境中编译。前端有新的依赖时提交依赖清单并在平台统一审核、安装和锁定，不假定微服务的 node_modules 会进入浏览器构建。

`components/packs/` 内的 TypeScript 文件由平台统一检查，React/React DOM/JSX runtime 使用宿主单一依赖；不要在组件目录另装 React。安装后执行平台 `web` 目录的 `npm run build`，同时验证类型和实际打包，不以独立预览成功代替接入检查。

Node 模板的三条 `/platform/v1/` 路由属于统一适配层；`handler.mjs` 属于可修改的业务层。JSON Schema 根据自己的输入输出维护。模板含单进程持久调用回执；有外部写入时仍要处理业务写入与回执落盘之间的失败窗口。

本骨架验证组件与服务接线，不自带独立账号服务器或第二套聊天系统。固定页入口为 `/#components`；对话输入 `/component example.interaction`。Python 同步能力直接显示结果；Node 能力按现有协议进入平台后台任务，组件轮询显示状态并在完成后呈现结果。
