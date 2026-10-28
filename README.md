# DuoFlow Platform · 双行主框架

版本 **v0.02**。统一管理业务模块、身份与范围授权，将模块公开的业务按顺序连接，让同一项业务通过页面和对话继续办理，并查看整体业务图与执行记录。

- [功能介绍页](docs/index.html) · [独立交互演示](docs/demo.html)
- [功能与支持范围](docs/FEATURES.md) · [源码阅读](common-platform/docs/源码阅读指南.md)
- [主框架说明](common-platform/docs/主框架说明.md) · [模块接入标准](common-platform/contracts/registration/v1/README.md)
- [模块套件](https://github.com/li-bruce/duoflow-module-kit) · [版本对应](COMPATIBILITY.md)

## 功能

本地后续候选加入[独立监控服务](common-platform/docs/独立监控服务设计.md)：健康探测和模块负载进入独立进程，主API每5秒只取汇总。监控独立启停，缺失/过期保持未知；不改变业务授权与正式状态。

本地后续候选加入[模块AI服务与配置](common-platform/docs/模块AI服务与配置.md)：模块声明实际AI服务，管理员在模型连接界面逐服务选择停用、平台模型连接或模块自有API，并代理配置与测试。密钥分别由各自私有配置保存，调用按当前权限和配置修订执行，无静默回退；已发布v0.02标签保持原样。

| 特性 | v0.02 内容 |
| --- | --- |
| 模块管理 | 服务发现、健康观察、双向验收、发布与停用 |
| 身份和权限 | 独立本地身份、范围授权、对象事实复核、远端中央决策 |
| 两种入口 | 页面和对话共用组件、业务动作及正式对象 |
| 业务组合 | 选择完整或部分业务流、连接输入输出、逐步确认和等待人工办理 |
| 组合权限 | 明确关联模块角色及范围，整组分配，每步复核当前对象权限 |
| 整体业务图 | 查看业务、状态与数据连接，检索节点并关联实际调用及组件实例 |
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

固定入口 `/#module-hub`（模块业务）、`/#compositions`（组合业务）、`/#flows`、`/#chat`、`/#services`、`/#devtools`。中立实验室通过 `/flow framework-lab.workflow` 打开，真实外部模型为可选配置。组合操作见 [使用与角色说明](common-platform/docs/组合业务运行与角色.md)。

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

本版提供最多 20 步的顺序组合、明确字段连接、共享业务组件、角色组合、持久运行和整体业务图。业务状态由各模块决定；服务短暂离线时保留办理，结果不明的写入需核对原调用记录。条件分支、并行、循环、跨服务自动补偿、外部统一登录及真实模型联调仍需另行建设或验收。

## 介绍页

运行 `python -m http.server 8040 --bind 127.0.0.1 --directory docs` 后打开 `http://127.0.0.1:8040/`。功能介绍与交互演示分开；GitHub Pages 可选择 `docs/` 目录托管。运行工作台使用上方启动步骤。

## 并行的业务状态协同

除按序办理的组合外，平台支持独立业务协调中心：各模块并行运行，状态变化提交到中心，按明确订阅向多个模块分发；公开申请由管理员处理后交回来源模块实际执行。入口是“业务协同”，Node/Python模块SDK提供事务Outbox/Inbox。详见[并行业务状态协同](../docs/并行业务状态协同.md)。
