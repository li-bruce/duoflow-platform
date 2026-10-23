# DuoFlow Module Kit · 双行模块套件

版本 **v0.01**。将业务要求整理为可在页面与对话中使用的模块，提供协议、Python/Node SDK、模板、独立检查工具和 `duoflow-module` Skill。

- [功能介绍页](docs/index.html) · [独立交互演示](docs/demo.html)
- [功能与支持范围](docs/FEATURES.md) · [源码阅读](docs/ARCHITECTURE.md)
- [开发与使用](docs/DEVELOPMENT.md) · [业务建模和持续同步](docs/OBSERVER.md)
- [版本对应](COMPATIBILITY.md) · [v0.01 变更](CHANGELOG.md)

主框架：[duoflow-platform](https://github.com/li-bruce/duoflow-platform)。业务建模依赖：[business-observer](https://github.com/li-bruce/SoftwareStatusObserverSkill)，精确提交和文件清单见 `business-observer.lock.json`。

## 独立准备

Python 3.12、Node 24。以下命令从本仓库根运行；POSIX 使用 `.venv/bin/python`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
npm ci
New-Item -ItemType Directory -Force work | Out-Null
.\.venv\Scripts\python.exe scripts/module_kit.py scaffold --template tool-python --module-id example.text --title "文字工具" --output work/example-text
.\.venv\Scripts\python.exe scripts/module_kit.py check work/example-text
.\.venv\Scripts\python.exe work/example-text/tests/self_test.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

生成 Node 流程模块时选择 `--template workflow-node`，在生成目录运行 `node --test tests/workflow.test.mjs`。该模板使用 Node 内置 HTTP、SQLite，无额外服务端 npm 依赖。Python 原生工具在接入后使用平台可信身份与授权；独立测试使用合成授权。

## 使用 Skill

在支持目录 Skill 的开发工具中加载 `skills/duoflow-module/`，同时提供本仓库路径。可复制完整目录到工具支持的 Skill 目录；其引用资料仍须由本仓库提供。描述业务目标、参与职责、输入输出和验收场景，调用 `$duoflow-module`。

Skill 先使用固定版本的 business-observer 明确业务、状态和预期，再完成模块实现，持续维护源码、参数、业务图与真实运行的关联。基础代码模板可以先生成；业务实现以已明确的业务为依据。安装与接线见 [Observer 使用说明](docs/OBSERVER.md)。

模块运行依靠业务代码和 SDK。Skill、图形开发工具和提示资料属于开发支持。主平台单独运行；本仓库可独立生成、检查并测试模块，最终安装、真实用户授权和发布验收在主平台完成。

## 文档与演示预览

运行 `python -m http.server 8040 --bind 127.0.0.1 --directory docs`，打开 `http://127.0.0.1:8040/`。GitHub Pages 使用仓库的 `docs/` 目录即可托管相同静态页面；站点启用由仓库设置管理。
