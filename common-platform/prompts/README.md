# 给其他开发 AI 使用

v0.02 模板加入原子登记、实际业务节点映射及公开业务流边界，套件元数据为 1.1.0。具体协议见 `contracts/module-graph/v1/` 与 `contracts/flow-exports/v1/`，接入 1.1 见 `contracts/registration/v1.1/`。真实服务回调、实例观察、对话和授权有独立回归；最终验证范围见发布仓库的 VALIDATION.md。

平台实现 composition 1.0：有序业务组合、显式字段连接、独立运行记录、逐步确认、人工办理等待、后台推进和调用凭据恢复。页面与对话引用同一组合运行；整体图关联业务、状态和数据节点。组合角色明确关联各模块角色与各自范围，由管理员整组分配，模块对象检查仍独立执行。协议和用法见 `contracts/compositions/v1/`、`docs/组合业务运行与角色.md`。

现行接入另包含 registration 1.0：模块交付公开声明、实际函数绑定与双向握手，主框架七项检查全部通过后才允许发布。协议 Schema、Python/Node SDK 和两侧完整架构说明均随源码包导出；见 `contracts/registration/v1/`、`docs/主框架说明.md`、`docs/模块框架说明.md`。旧清单发现不能替代当前验收，实际用户授权与业务正确性仍单独验证。

新模块优先使用 **[模块开发快速提示词.md](模块开发快速提示词.md)**，同时提供同批 `templates/module-kit/`、`scripts/module_kit.py` 和 [模块模板使用手册](../docs/模块模板使用手册.md)。快速提示词只保留业务填写、模板使用和验收步骤；精确协议与源码参考仍在下面两份完整提示词。三份提示词和快照由同一个生成器维护，不能把快速提示词单独当成完整 SDK。

两份提示词均要求交付可发现、可进入、可读取、可执行的访问矩阵，并明确当前没有游客模式、自助注册、公开入口策略或 iframe SSO。游客隔离与登录升级是设计目标，不是可直接填入现有 Schema 的字段。通用包中的现行入口见 [架构地图的权限说明](../docs/通用框架架构与代码地图.md) 和 [模块生命周期与隔离页面](../docs/module-lifecycle-design.md)。

开发“同一组件用于 Web 与对话”的模块时，优先交付本目录生成的 **[组件开发提示词.md](组件开发提示词.md)**，包含独立组件契约、SDK、Python 原生与 Node.js HTTP 骨架；复杂远程服务还同时提供下面的完整模块提示词。两份提示词由同一个生成器重建并携带相同源码指纹。

直接复制 [模块开发与改造提示词.md](模块开发与改造提示词.md) 的**全文**，填写开头“使用者填写”里的业务目标、项目位置、接入方式和验收场景，然后发给负责那个模块的 AI。也可以将该文件作为附件交给它，并补充填写内容。真实密钥通过服务端配置提供，不放在提示词里。

这份文件与具体业务无关，同时适用于新建模块和改造旧项目。它附有完整的本地模块模板、远程服务声明、调用样例、双端注册方式和当前实现边界；远程开发 AI 即使没有本框架源码，也可以据此实现服务。本地模块运行和最终双端联调仍需要平台环境。

复杂业务另有 workflow v1：`flows/packs` 中的共享步骤组件、权威业务对象引用、个人草稿与对话导航、分块文件及业务任务适配。组件提示词附带 `@platform/workflow-sdk`、workflow/files/jobs Schema 和中立 Python 对象模板。它与旧单组件 interaction v1 并存；两种接口的字段不可混用。具体示例的真实容量、外部存储和模型验收看模块报告，不能从公共协议推断已经验证。

本轮已加入独立 access v1：模块定义权限、带范围的角色模板及办理视图，平台管理真实用户授权，模块根据仓储事实检查对象范围与正式状态。两份提示词均导出 `contracts/access/v1`、管理/业务中心路由、合成权限附件与单独授权示例，以及 `templates/remote-access-client` 的 Node 中央决策客户端。原 manifest/invoke 和 actor JSON 保持不变，新角色不添加到固定身份枚举。

“我的业务”通过 flow provider 实时读取授权对象，并恢复本人私人流程；当前 workitem_sources 是注册元数据，不是自动执行引擎。Python 使用 `ctx.access`，Node 等远端使用 `X-Platform-Access` 短时票据请求中央判断。未注册 access 的旧模板仍是固定角色示例；有状态模块应另外交付正式对象适配、权限包和职责流程，不能仅复制只读样例就声称完成业务集成。远程接入顺序为发现禁用、安装需要的流程/组件、注册权限、完成七项接入检查、通过后发布、授予范围权限。

v0.4 主示例使用 manifest 1.2 / invoke 1.1，附带 `contracts/v1.2/` 全部公共 Schema，涵盖完整身份、多角色、文件输入、workspace 结果及 skills。旧 manifest 1.1 / invoke 1.0 模板只在明确的 legacy 附录保留；不能把新清单和旧适配器混用。文档还说明对话恢复、消息位置分支、权限撤销、独立本地 Git 提交与外部 OIDC 尚未实现的边界。

例如补充：“改造现有 Node.js 项目；把文字转换功能接为 remote 服务；网页和对话都可以输入文字、执行并查看同一结果；已有源码位于……”。其他业务只需替换这段需求和验收条件，公共接入规则继续复用。

| 文件 | 用途 |
|---|---|
| `模块开发快速提示词.md` | 新模块的简明入口，与同批模板和 SDK 一起使用 |
| `模块开发与改造提示词.md` | 对外使用的完整提示词，包含当前版本和契约指纹 |
| `组件开发提示词.md` | 共享组件、流程及两端宿主的完整参考 |
| `framework-contract.snapshot.json` | 相应机器可读契约、样例、源码清单与指纹 |
| `_source/component-developer.md` | 共享组件、流程宿主及范围权限的通用说明正文 |
| `_source/module-developer.md` | 开发者维护的通用指令正文 |
| `_source/module-quickstart.md` | 快速提示词的可维护正文 |
| `../scripts/build_module_developer_prompt.py` | 从公开代码和模板导出附录，生成/检查三份提示词及快照 |

## 更新如何保证

框架源码、前端契约、模板、相关说明和依赖发生变化后，一致性检查会检测旧提示词。源码新增或删除也会被检测。开发 AI 先复核语义、调整源提示词和生成器，再重建；脚本能提取版本、心跳参数、限额、模板和示例，**不能代替开发者判断文字是否仍准确**。

项目根目录和框架目录均已写入 `AGENTS.md` 维护规则。`scripts/start.ps1` 启动前执行检查，pytest 中也有一致性与真实契约验证。以后在当前任务中修改相关功能，会将同步提示词作为交付步骤。

在 common-platform 目录执行：

```powershell
# 只检查，不改文件
.\.venv\Scripts\python.exe scripts/build_module_developer_prompt.py --check

# 完成语义复核后重建
.\.venv\Scripts\python.exe scripts/build_module_developer_prompt.py --write --reviewed

# 验证导出契约；使用唯一目录，避免清理并行任务的临时数据
$promptTestBase = Join-Path (Get-Location).Path ('data/coordinator-checks/prompt-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest tests/test_developer_prompt_contract.py -q --basetemp $promptTestBase
```

只有生成文件需要直接交给其他开发 AI；无需携带真实配置或数据目录。该机制不访问模型、不联网、不读取凭据，也不增加后台定时服务。已经复制到别处的旧版本不会自动更新，升级时重新提供最新文件即可。指纹用来识别公开源码快照，并非数字签名或在线版本查询接口。

当前授权以单个用户为主体；没有组授权、委托业务管理员或外部 OAuth/OIDC 登录。本人授权或定义变化会使旧 scoped 私人实例及历史结果快照失效，尚无按项目的旧草稿自动迁移。远程短时票据也不是长任务委派；多副本密钥治理、远程分布式交接与事件调度须单独建设。上述实现边界必须跟随源码继续同步，不能只刷新哈希。
