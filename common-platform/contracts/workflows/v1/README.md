# Workflow 对象分页与页面组合 v1

业务模块是对象状态与权限的唯一权威。平台 Flow 保存个人草稿、导航和对象引用；查询前后核验登录身份、个人 Flow 所有权、授权修订、安装定义、能力与可用性，I/O 后还比较个人 Flow 修订。浏览器不能指定能力、服务地址、可信身份或详情 object_ref。

| 固定 adapter 操作 | input | data | data 大小上限 |
|---|---|---|---|
| objects.list | `{object_type?,cursor?,limit?}` | `{objects,next_cursor}` | 128 KiB |
| objects.read | `{object_ref}` | `{projection}` | 1 MiB |
| objects.detail | `{object_ref,section,cursor?,limit?}` | `{object_ref,revision,items,next_cursor,total}` | 512 KiB |

目录中的 projection 应为摘要；选中对象的 read 投影承载当前有界工作状态；大型行表、问题、历史与其他业务详情按 section 分页。平台不理解 section 的业务意义，具体白名单由业务服务验证。section 格式为 `^[a-z][a-z0-9_.-]{0,119}$`；cursor 是 1 至 2048 字符的不透明字符串。limit 为 1 至 50 的整数，默认 20；返回条数不能超过请求 limit。next_cursor 必须是有效 cursor 或 null，旧本地 adapter 可暂缺省并按 null 处理，远端不能省略。

游标不授予权限。业务服务必须每页重验 actor 与对象范围，并将游标绑定对象类型、对象/section 及相关修订；数据变化应返回 409，客户端从首页重读。detail 返回的 object_ref 必须精确匹配请求，revision 必须为正整数，total 必须为非负整数。列表所有对象类型必须匹配请求 object_type。所有 JSON 仍经过秘密、内联文件字节及总计 2 MiB 限额检查。超出分操作预算是模块协议错误，平台不静默截掉条目或授权动作。

平台同源 POST 路由位于 `/api/v1/flows/instances/{id}`，均检查现有会话和 CSRF：`/objects/list` 接收 `{object_type,cursor?,limit?}`，`/detail` 接收 `{section,cursor?,limit?}`。详情对象只从当前个人 Flow 的 object_ref 取值；list 的 object_type 只能是 provider.object_type 或 provider.related_object_types 中声明的类型。related_object_types 最多 5 个，自身类型隐含允许。

实例 GET 为自身及相关类型各取首页 20 条，保留合并 objects 数组，另返回 `object_pages:[{object_type,next_cursor}]` 供各目录继续分页。最终 Flow 响应独立限制为 2 MiB。已授权对象、动作与详情不能用扩大总响应限额代替分页设计。

定义可选 page_groups，包含 1 至 12 个 `{id,title,steps}` 分组；组 id 唯一，每组有 1 至 8 个不重复且真实存在的 step id。省略时页面沿用单步骤展示。catalog 保留完整命令 schema，实例返回 `step_actions:{stepId:[action摘要]}`，并以 actions 返回当前步骤摘要。摘要包括 id、title、available 及已声明的能力/版本、creates_object、target_step、mode，不重复静态 command_schema。

页面组合只改变呈现；动作仍必须在实例当前 cursor 声明。跨步骤操作先经普通 PATCH 更新个人 cursor 和草稿，再经已有 actions 路由执行，保持角色、对象、状态、CAS、幂等与审计守卫。对话继续只展示当前步骤。中立 Python 模板实现私有对象 list/read 和既有 create/edit 命令，未实现 detail、文件和长任务时明确返回 501。
