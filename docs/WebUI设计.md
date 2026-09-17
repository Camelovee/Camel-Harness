# Camel Harness WebUI 设计

## 1. 产品定位

WebUI 不是 CLI 的简单搬运，而是一个“本地工作区工作台”：用户选择本地目录和会话，给 Agent 一个工作目标，观察它的规划、模型增量、工具调用、权限请求和最终结果。首版优先保证可解释、可恢复和权限边界清晰，再扩展为完整的软件开发工作台。

视觉参考 `deepseek-harness-java` 的本地 Web 控制台定位：对话只是入口，工作区、任务审批、插件和模型配置都属于同一个运行时控制台；本项目保留 React 技术栈，但采用本地无外部字体依赖的浅色实现。

## 2. 首版信息架构

```text
┌──────────────────────┬────────────────────────────────────────┐
│ 工作区/会话侧栏       │ 主工作区                                │
│ 品牌、选择本地工作区  │ 顶部：会话标题 / 对话 / 轨迹              │
│ 工作区树、会话树      │ 中部：消息流、工具卡片、内联审批卡片       │
│                      │ 底部：固定 Composer + 权限模式           │
└──────────────────────┴────────────────────────────────────────┘
```

- 左栏：品牌、选择本地工作区、工作区树；每个本地目录下面展开多个会话，工作区级提供新建会话，会话级提供删除会话。
- 主区：顶部会话标题和“对话 / 轨迹”切换，中部为消息历史、实时文本增量和可展开工具卡片，底部为脱离消息滚动容器的固定输入框和权限模式。
- 审批卡片插入对应的 assistant 回答之后，显示工具名、风险摘要、调用参数和“允许/拒绝”；不能把审批请求放到主区顶部抢占消息布局。
- 视觉：浅灰应用背景、白色内容卡片、细边框和蓝/青状态色，橙色只作为品牌与危险操作强调色，避免把 Agent 工作状态做成高噪声仪表盘。

## 3. 后端边界

FastAPI 位于 `camel_harness.backend.api`，只负责请求校验、状态码、CORS 和 SSE 输出；产品级编排由 `camel_harness.harness.Harness` 完成，低层循环位于 `camel_harness.agent_loop`，运行时装配位于 `camel_harness.harness.runtime`。浏览器不得直接接触 Provider、Session 存储或 SDK 类型。

HTTP 内部按四类职责封装：`router.py` 只负责聚合；`routes/` 按资源注册端点；`contracts.py` 维护 Pydantic 请求模型；`responses.py` 统一 SSE 响应。启动期由 `HttpDependencies` 一次性发布完整 Harness，路由统一通过 `require()` 获取，不再维护第二套 ApplicationService 和 DTO。路由文件不承担应用生命周期或事件帧编码。

核心接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 运行状态 |
| GET | `/api/workspaces` | 查询已有本地工作区及其会话 |
| POST | `/api/workspaces/select` | 调用本机目录选择器 |
| GET/POST | `/api/sessions` | 查询/创建会话 |
| GET/PATCH/DELETE | `/api/sessions/{id}` | 会话元数据管理 |
| GET | `/api/sessions/{id}/messages` | 恢复消息投影 |
| POST | `/api/sessions/{id}/turns` | 发起 Turn，返回 SSE |
| GET/POST | `/api/approvals`、`/api/approvals/{request_id}` | 查询和处理浏览器审批 |

Turn 使用 POST + SSE，而不是 GET EventSource：这样可以携带较大的用户输入、权限模式和步数约束。事件沿用现有规范事件和 sequence；客户端按 `type` 更新消息、工具活动和状态，不解析厂商 chunk。工具进入浏览器审批队列后发送 `approval_requested`，该事件严格位于当前 assistant 的 `message_completed` 之后、`tool_result` 之前。

## 4. 权限与可靠性设计

`ConsoleApproval` 只适合 CLI。Web 入口使用 `WebApproval`：危险工具调用会创建一次性 request，Agent 协程挂起，浏览器通过审批接口恢复或拒绝；五分钟无响应自动拒绝。后续可把审批队列从进程内存迁移到 Task/Session 事件模型，以支持多进程和断线恢复。

首版建议默认使用 `default` 模式；`plan` 用于只规划不执行，`accept_edits` 用于可信工作区内编辑，`yolo` 仅保留给明确的本地实验。UI 必须显示当前模式，服务端仍以工具权限策略为最终裁决。

## 5. 前端状态模型

```text
idle -> submitting -> streaming -> waiting_approval -> streaming -> completed
                   \\-> failed/cancelled
```

前端至少维护：`workspaces`、`activeWorkspace`、`activeSession`、`messages`、`streamStatus`、`pendingApprovals`、`permissionMode` 和 `lastSequence`。审批请求作为消息流中的临时卡片维护，批准或拒绝后立即移除，工具结果仍按事件顺序保留；超时未处理的卡片在工具结果到达后移除。断线后先重新读取消息历史，再决定是否展示“运行中”；不要依据浏览器内存中的半截回答直接恢复。消息区使用独立滚动，Composer 作为主工作区底部固定区域，不会随消息列表滚出视口。

Markdown 消息与用户纯文本消息分开处理：Markdown 容器使用正常空白折叠，避免渲染 HTML 标签之间的换行产生额外空行；用户输入仍使用 `pre-wrap` 保留手动换行。流式 Markdown 在渲染前清理末尾无意义换行，但不压缩代码块或正文内部的有效空行。一个 Turn 内每个 `step_id` 使用独立的 assistant 消息，审批卡片和工具结果按 SSE 到达顺序插入，避免后续 Step 的回答回填到前一个 assistant 消息。审批实时状态以 Turn SSE 为准，`GET /api/approvals` 只用于初始化、切换会话和页面恢复，不使用定时轮询。

Composer 提供按 Turn 生效的“思考”开关。启用后，后端请求支持的 Provider 返回 reasoning/thinking，并通过 `reasoning_delta` 实时投影到 assistant 消息中的可折叠“思考过程”卡片；最终正文仍只消费 `text_delta`。完整 `ReasoningBlock` 可从会话历史恢复，但 Provider Adapter 在下一轮请求转换时过滤该块，防止观察数据污染模型上下文。若当前模型不支持思考能力或没有返回相关增量，前端不创建空卡片。

### 5.1 React 代码分层

```text
app/App.tsx
  -> features/agent
  -> features/workspaces
  -> shared

features/* -> shared
shared     -> React/第三方库，不依赖 app 或 features
```

- `app` 只维护顶层页签、错误提示、删除确认及 feature 之间的回调组合。
- `features/workspaces` 封装工作区/会话 API、状态 Hook 和侧栏组件。
- `features/agent` 封装历史、审批、Turn SSE、事件投影、Composer、消息与轨迹组件。
- `shared` 提供 HTTP Client、跨 feature 类型、Markdown/文本展示函数和基础反馈组件，不保存业务状态。
- feature 之间禁止直接导入；例如 Agent 完成后通过 app 注入的 `refreshWorkspaces` 回调刷新会话元数据。
- 当前规模不引入 Redux、Context 总线或前端领域层；状态范围扩大到文件树、diff 和后台任务后再评估。

## 6. 分阶段路线

1. 当前切片：FastAPI、SSE、会话恢复、React/Vite 浅色本地工作区工作台、浏览器审批。
2. 工作区开发体验：文件树、代码预览、diff 审阅、可取消 Turn、工具调用详情和事件时间线。
3. 任务运行时：把长任务、子 Agent、后台执行和通知接入 Task 上下文，前端增加任务看板。
4. 生产化：用户认证、工作区授权、审计导出、并发限制、跨进程事件总线和可观测性。

## 7. 暂不做的事情

- 工作区选择由本机 FastAPI 进程打开原生目录选择器；服务端会规范化路径，工具权限以会话所属工作目录作为边界。
- 不推断或伪造隐藏思维链；只展示 Provider 明确返回的 reasoning 摘要或 thinking 内容，并标记为模型提供的信息。
- 首版不把工具完整大结果全部塞进聊天上下文，继续使用现有大结果落盘与引用机制。
- 首版不引入全局状态管理库；等任务、文件树和多面板交互稳定后再评估。
