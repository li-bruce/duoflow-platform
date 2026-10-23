# DuoFlow Platform · 双行主框架

版本 **v0.01**。统一管理业务模块、身份与范围授权，让同一项业务通过固定页面和对话继续办理，并保留正式状态、私人草稿和执行记录。

- [功能介绍页](docs/index.html) · [独立交互演示](docs/demo.html)
- [功能与支持范围](docs/FEATURES.md) · [源码阅读](common-platform/docs/源码阅读指南.md)
- [主框架说明](common-platform/docs/主框架说明.md) · [模块接入标准](common-platform/contracts/registration/v1/README.md)
- [模块套件](https://github.com/li-bruce/duoflow-module-kit) · [版本对应](COMPATIBILITY.md)

## 功能

| 特性 | v0.01 内容 |
| --- | --- |
| 模块管理 | 服务发现、健康观察、双向验收、发布与停用 |
| 身份和权限 | 独立本地身份、范围授权、对象事实复核、远端中央决策 |
| 两种入口 | 页面和对话共用组件、业务动作及正式对象 |
| 办理恢复 | 私人草稿、导航、执行回执和刷新恢复；对话分支保留对象引用 |
| 开发调试 | 声明检查、业务状态图、真实状态与执行状态分别观察 |
| 可携带交付 | 协议、模板、SDK、文档、合成中立模块和回归测试 |

## 启动

保留 `common-platform/` 目录。Python 3.12、Node 24；依赖均安装在本仓库内。

```powershell
cd common-platform
.\scripts\setup.ps1 -Python python -Node node -Npm npm.cmd
.\.venv\Scripts\python.exe scripts/build_module_developer_prompt.py --check
.\scripts\start.ps1 -Port 8030 -DataDirectory .\data\local
```

浏览器打开 `http://127.0.0.1:8030/`。初始身份是 `admin / demo-admin-2026` 和 `member / demo-member-2026`，由本仓库合成配置初始化；部署时按说明设置独立身份与配置。停止使用 `.\scripts\stop.ps1 -DataDirectory .\data\local`，脚本核对进程身份。

固定入口 `/#flows`、`/#chat`、`/#services`、`/#devtools`。中立实验室通过 `/flow framework-lab.workflow` 打开，真实外部模型为可选配置。

## 验证

```powershell
New-Item -ItemType Directory -Force ..\work\pytest | Out-Null
$env:PLATFORM_DATA_DIR = (Join-Path (Get-Location) '..\work\test-data')
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp ..\work\pytest\run-new
cd web
npm ci
npm test
npm run build
cd ..
.\.venv\Scripts\python.exe scripts/build_handbooks.py --check
```

提示词由 `prompts/_source/` 和公开源码生成。维护方法见 [prompts/README](common-platform/prompts/README.md)。业务状态由模块保存；页面导航、调用状态与正式业务状态分别处理。

## 当前建设范围

已提供模块公开、统一调用和双端宿主。跨模块总业务图、数据连接、通用组合执行、组合级等待恢复仍是后续建设内容，见 [现状分析](common-platform/docs/模块公开与业务组织分析.md)。单用户中立演示与真实多角色、生产运行、外部模型联调分别验收。

## 介绍页

运行 `python -m http.server 8040 --bind 127.0.0.1 --directory docs` 后打开 `http://127.0.0.1:8040/`。功能介绍与交互演示分开；GitHub Pages 可选择 `docs/` 目录托管。运行工作台使用上方启动步骤。
