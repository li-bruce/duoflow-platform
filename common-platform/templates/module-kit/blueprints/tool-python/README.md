# __TITLE__

这是一个 Python 原生简单工具。修改 `backend/business.py` 中的纯函数即可替换文本处理逻辑；同时维护输入输出 Schema 和共享组件。它使用当前平台登录及机构范围授权，不含另一套登录、聊天或权限引擎。

## 先独立运行

生成包已经包含便携 SDK `backend/_module_kit.py`，只依赖 Python 标准库。执行：

```powershell
python tests/self_test.py
```

14 项离线检查覆盖文本边界、机构事实来源、拒绝授权、权限服务故障和结果契约。这里的合成权限服务只验证调用边界；真实 grant、撤权、宿主页面/对话、恢复与分支应在安装后联调，不能从离线检查推断通过。无需启动服务、配置账号或连接外部模型。

## 需要改哪些文件

| 文件 | 开发者工作 |
| --- | --- |
| `backend/business.py` | 纯业务函数；接收字典，返回 `summary` 和 `data`，不读取浏览器身份 |
| `backend/manifest.json` | 原生 Python 能力、Schema、页面和对话表单入口 |
| `backend/access-definition.json` | 模块权限 `.use`、职责 `.user`、必填机构范围 |
| `backend/handlers.py` | 薄平台边界，调用 SDK 并转换为 `CapabilityOutput` |
| `backend/_module_kit.py` | 随包供应的 SDK 1.0.0；升级时整体替换并重跑检查 |
| `frontend/Component.tsx` | 页面与对话共用的业务界面，只调用宿主 SDK |
| `frontend/interaction.json` | 共享组件定义及动作参数绑定 |

文本示例把 CRLF/CR 统一为换行，压缩每行空白并去掉全文首尾空白；字符数按 Unicode 码点计，行数包含保留的空行，片段数按空白切分，不代表中文分词或专业语义分析。最多输入 20000 个字符。

## 安装到平台

1. 将整个 `backend/` 复制到 `extensions/__PACK_NAME__/`，包含 `_module_kit.py` 和 `access-definition.json`。在 `extensions/installed.json` 的 `modules` 数组追加 `__PACK_NAME__`，保留现有条目。
2. 将整个 `frontend/` 复制到 `components/packs/__PACK_NAME__/`，在 `components/installed.json` 的 `packs` 数组追加 `__PACK_NAME__`。
3. 按平台维护流程执行类型检查、前端生产构建、提示词同步检查，再重启受控平台进程。包代码以 `__file__` 解析本地 SDK/业务文件，不依赖启动目录或全局 `sys.path`。不允许普通用户上传任意 Python 后执行。
4. 由具有 `identity.manage` 的管理者，为实际用户授予 `__MODULE_ID__.user`，填写该用户真实 `organization_id`。安装只注册权限定义，不发放授权；`member` 是旧清单兼容声明，不能替代真实 grant。仅有 `system_admin` 也不自动获得工具使用权。
5. 登录获授权的用户，进入 `/#components`，打开 `__MODULE_ID__.tool`；在对话中输入 `/component __MODULE_ID__.tool`。两端使用同一能力 `__MODULE_ID__.analyze`。固定表单能力入口也经过同一后台授权。

检查安装复制是否完整：`backend/` 的四个业务文件及 `_module_kit.py` 必须在同一扩展目录；组件目录必须保留文件名 `Component.tsx`、`interaction.json`。注册后先核对清单中能力、模块 ID、组件动作版本和 access 映射一致，再做真实账号验证。具体 CLI 与完整命令见平台的《模块模板使用手册》。

## 权限、数据与恢复边界

SDK 始终以平台注入的 `ctx.actor.organization_id` 作为本工具的可信范围事实，机构缺失、访问服务缺失、检查失败或拒绝均停止执行。输入不能传入 `organization_id`、角色或权限来替换身份。以后增加持久业务对象时，必须从业务仓库读取对象机构等事实并逐次授权，不能继续用操作者机构替代对象归属。

| 环节 | 条件 |
| --- | --- |
| 发现 / 打开 | 登录、模块和能力可用，平台目录判断存在 `.use` 授权 |
| 读取既有结果 / 恢复 | 本人实例及当前平台授权快照仍有效 |
| 执行动作 | 以上条件，加本人机构与完整 grant 范围匹配；输入 Schema 与业务边界都通过 |
| 分支 | 平台复制本人已保存的组件状态；不会自动再次执行文本处理 |

没有正式业务对象、状态流转或待办目录，因而 access 的 `experiences` 与 `workitem_sources` 为空；简单工具不冒充“我的业务”流程。CSRF、修订、幂等、启停和私人实例由平台宿主处理。游客、通知、SSO、委派、长任务与外部副作用均未实现。

本地 manifest 使用平台原生结构；manifest 1.2 / invoke 1.1 是远程 HTTP 接入边界，不能把两者混填。需要远程 Node 流程时选择对应模板。
