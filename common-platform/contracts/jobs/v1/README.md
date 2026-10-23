# 任务观察与控制适配 v1

本轮任务启动通过已注册业务命令能力执行；该命令以平台 invoke 的既有幂等与权限边界返回业务服务的 job 引用。平台调用完成只表示启动请求完成，不代表异步业务任务成功。不能将一个 job_id 当成已完成业务结果，也不能由模型修改 job 或业务状态。

业务服务是任务状态唯一权威。固定适配接口均使用 workflow v1 包络，返回 HTTP 200 的当前观察结果；不采用 HTTP 202、回调、SSE 或轮询返回的临时 URL。

| 平台接口（前缀 `/api/v1/flows/instances/{instance_id}`） | 远端接口 | 返回 data |
|---|---|---|
| `GET /jobs/{job_id}` | `POST /platform/v1/workflow/jobs.status` | `{job}` |
| `GET /jobs/{job_id}/result` | `POST /platform/v1/workflow/jobs.result` | `{job,result,projection}` |
| `POST /jobs/{job_id}/cancel` | `POST /platform/v1/workflow/jobs.cancel` | `{job,projection}` |

输入中的 object_ref 来自服务端流程实例。服务端再次核验 actor 的有效角色、对象范围及 job 实际所属对象；cancel 另需 expected_revision。状态枚举 queued、running、done、failed、cancelled；done 是任务完成，并不自动表示业务审核通过。progress 为 `{completed,total}` 非负整数计数，completed 不得超过 total；总数为 0 时尚未确定或没有工作项，界面不能除以零。cancellable/retryable 是服务端投影，调用时仍需复核。错误采用受控 code/message，不能返回凭据、远端堆栈或模型供应商正文。

cancelled 仅在业务服务确认停止且未提交本次结果时返回；关闭浏览器、取消平台协程或 HTTP 超时不能证明远端已停止。不可取消的阶段返回状态冲突，已完成提交与审批不能借任务取消回滚。任务若仍在协作取消中，应继续返回 running；当前 v1 没有单独 cancel_requested 状态。

重启恢复、阶段检查点与安全重试由业务服务实现并分别验收。此适配器本身不提供任务调度、自动重放、多进程锁、跨服务事务、Temporal 或任意副作用“恰好一次”保证。Python 原生能力仍可使用已有平台 Run；需要此协议的本地任务服务可通过受控 extensions manifest 的 `workflow_adapter: "adapter:create"` 注册同契约工厂。该机制已经接通安装与运行，但没有默认本地任务引擎；未实现 jobs 操作的适配器返回 501。中立 Python 模板仅实现模块自有表中的 create/edit/read/list。
