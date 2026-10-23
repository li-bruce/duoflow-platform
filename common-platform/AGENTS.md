# 开发提示词与实际框架同步

用户要求提供独立于业务的开发/改造提示词，并在今后每次相关框架修改时重建匹配版本。这是本项目完成修改的固定步骤，适用于本任务后续轮次和其他读取本项目的开发 AI。

## 本地 Git 与统一接入边界

本目录按独立本地 Git 仓库维护；业务微服务在各自模块目录维护独立仓库。用户要求每轮修改结束后完成相关验证并提交本轮变更，采用中文规范提交，如 `feat(对话): 支持流程状态恢复`、`fix(权限): 阻止跨机构读取`。先检查状态与暂存内容，不提交运行数据、真实配置、密钥、日志和依赖目录，不自动推送远程。多任务并行期间由统筹任务提交框架，以免把尚未完成的他人变更混入。

统一适配接口见 `docs/integration-contract-v0.4.md` 和 `contracts/v1.2/`。日常业务调整发生在各微服务业务层，不直接修改协议/认证边界；需要跨服务协议变更时先更新统一契约、框架与所有受影响服务及测试，再重建开发提示词。

## 修改前

阅读 `prompts/README.md`、`prompts/_source/module-developer.md`，并核对本次相关代码。实际实现和校验器是事实依据；旧提示词不是扩展能力的证据。不得为了让提示词成立而伪造已经支持的接口。

## 修改后

1. 复核本次对能力字段、输入输出、注册、两端呈现、权限、存储、模型、远程协议、生命周期、心跳及启动方式的影响。
2. 更新 `prompts/_source/module-developer.md` 的语义说明；影响导出字段、示例或规则时，更新 `scripts/build_module_developer_prompt.py` 及契约测试。新建公共契约目录时，扩展生成器的安全源码清单；不纳入运行数据或凭据。
3. 在项目目录执行 `.\.venv\Scripts\python.exe scripts/build_module_developer_prompt.py --write --reviewed`。`--reviewed` 表示开发者已经完成上述语义复核，不能只刷新指纹掩盖说明失真。
4. 执行 `.\.venv\Scripts\python.exe scripts/build_module_developer_prompt.py --check` 和 `.\.venv\Scripts\python.exe -m pytest tests/test_developer_prompt_contract.py -q`；完成与实际改动相称的其他验证。
5. 交付时说明提示词已同步；若存在未支持的能力或未完成联调，清楚写出。不要直接修改生成的 Markdown 或 snapshot JSON。

启动脚本会只读检查提示词一致性；检测到过期时停止本次启动操作，提示复核与重建。它不会停止已运行的服务，也不会自动重写文档。直接启动 uvicorn 不经过该启动脚本，仍需执行上述检查。

源码指纹检测新增、删除和内容变动，不证明自然语言的语义正确。脚本不读取 `data/`、`.env`、日志或密钥，不访问网络。输出使用合成示例；不把本地演示账号、真实供应商配置或业务名称写入可携带提示词。

复制到其他项目/任务的旧提示词不会自动更新。每次交付和升级都提供最新生成文件，不声称已修改用户保存的副本。以上同步工作在既有授权范围内直接完成，不额外要求用户确认。

## 共享组件开发任务

`prompts/组件开发提示词.md` 同样由上述生成器维护。源文件是 `prompts/_source/component-developer.md`；生成物包含实际 SDK、组件 Schema、Python 与 Node.js 骨架。`prompts/模块开发快速提示词.md` 是简明入口，源文件为 `prompts/_source/module-quickstart.md`，与同批模板/SDK 一起交付。每次涉及组件注册、实例 API、宿主行为、后台动作或模板变更，复核并重建三份提示词及快照。

组件业务任务默认维护自身仓库及授权安装的 `components/packs/<pack>/`、`extensions/<module>/`。`app/interactions.py`、`app/interaction_routes.py`、`web/src/interactions/` 和 `contracts/interactions/` 为主框架通用接线边界；业务任务发现问题向主框架任务报告，避免自行修改协议或身份逻辑。新增任务交付时记录实际使用的提示词指纹和 SHA-256。

涉及对话呈现层的修改必须用生产构建后的真实浏览器验证进入对话、组件动作、后台结果与刷新恢复；开发模式或 jsdom 测试不能代替这一检查。当前 CopilotKit 只通过公开 `v2/context` 和 `v2/headless` 接口维护本地呈现注册表，完整 Provider 需要真实 Runtime 或云连接，不能配置假地址绕过初始化。
