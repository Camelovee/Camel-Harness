# Camel Harness 项目约束

本文件对整个仓库生效，只记录不可违反的项目约束。

## 代码规范

- 标识符使用英文；代码注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界条件和不变量，不逐行翻译代码。
- 保持实现简洁；只有存在真实多实现或扩展需求时才新增 ABC、转发层或目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

## 核心边界

```text
Backend / CLI -> Agent -> LLM
                   +----> Tools
                   +----> Session
                   +----> ContextManager -> Compaction / Resources
```

- Python 源码分为 `camel_harness`、`camel_backend` 和 `camel_cli` 三个根包；Backend 与 CLI 互不依赖。
- `camel_harness` 只包含 `agent` 和 `llm` 两个功能包；依赖只能沿 `camel_backend/camel_cli -> camel_harness.agent -> camel_harness.llm` 流动。
- `camel_harness.llm` 拥有规范消息、模型请求、工具定义和流事件，不得依赖 `agent`、Backend 或 CLI。
- LLM 请求和事件不得包含 `session_id`、`run_id`、`turn_id` 或 Agent 事件序号；这些运行坐标由 `Agent` 注入 `AgentEvent`。
- 不单独建立 `agent_loop` 模块或对外函数；`Agent` 内部完整编排会话、ReAct Turn、上下文压缩、资源加载和完整消息保存点。
- 一个 `Agent` 实例只绑定一个 Session 和 Workspace，同时最多运行一个 Run；Backend 只能在入口层按 `session_id` 定位 Agent，不得让 Agent 管理 Session 字典。
- `Agent` 的公开表面只包含 `run()`、`steer()`、`abort()`、`resume()`、`wait_for_idle()`、`subscribe()` 和 `close()`；Session CRUD、工作区选择、审批查询和扩展诊断由入口层持有。
- Agent 必须持有当前 Run 的执行任务；WebUI、CLI 和子 Agent 通过 `subscribe()` 观察事件，不得通过消费 Agent async generator 驱动运行。
- 运行中输入只使用一条内存 steering FIFO，并在完整 Turn 后消费；不得提前加入 Follow-up 队列、持久化 Inbox、全局调度器或可脱离 Agent 生命周期的后台 Run 系统。
- `AgentEvent` 必须是 Agent/Turn/Message/Tool/Approval 生命周期的判别联合，不得恢复通用 `type + data` 容器；Provider 增量只能出现在 `message_update.assistant_event`。
- 事件可用内部基类共享运行坐标，但公开 `AgentEvent` 仍须保持具体事件的判别联合。
- 公开事件使用 `session_id + run_id + turn_id`运行坐标，`sequence` 必须在整个 Run 内单调递增。
- `agent_end` 是正常、失败和取消 Run 的唯一终态事件；可恢复运行错误通过该事件传递，不在其后再抛出破坏 SSE。
- `camel_harness.agent.runtime` 是唯一完整组合模块；Agent 不得依赖 Backend 或 CLI。
- HTTP 路由只做请求校验、状态码和输出投影。

## LLM、工具与会话

- 默认 Provider 为 Anthropic，同时支持 OpenAI；两者必须使用官方 Python SDK。
- 厂商对象只能存在于 `camel_harness.llm`，对外统一为 `LlmStreamEvent`。
- 规范消息必须使用 `Message = UserMessage | AssistantMessage | ToolResultMessage`；系统提示通过 `LlmRequest.system_prompt` 单独传递，不得恢复通用 `role/content` 消息容器。
- `AssistantMessage.stop_reason` 必须使用厂商无关的 `LlmStopReason`；它与 Agent Run 级 `StopReason` 是两个独立概念。
- `length` 结束的工具调用禁止执行，必须写入错误工具结果以保持消息链完整。
- 模型增量实时转发；只有流正常结束后才能保存完整 assistant 消息，禁止保存 chunk。
- 中断必须向 LLM、审批和工具协程传播；不得保存未完成 assistant，已落盘但缺少结果的工具调用必须补充错误 `ToolResultMessage`，恢复时不得自动重试历史工具。
- `Tool` ABC 及 `ToolContext`、`ToolCall`、`ToolResult` 位于 `camel_harness.agent.tools.types`；给模型使用的 `ToolDefinition` 位于 `camel_harness.llm`，只包含最终工具名、描述和输入 Schema，不感知 namespace。
- 新工具通过 `ToolProvider` 注册，不得修改 Agent 主流程；工具执行必须经过参数校验、权限、审批、超时和结果归一化。
- Skill 只保留发现和三层按需加载：系统上下文只列名称与描述，`load_skill` 读取正文或支持文件；不提供 Skill 写入、演化、回滚和评估链路。
- Skill 来源为内置、项目级（工作区 `.camel/skills`）和用户级（`~/.camel/skills`），同名优先级依次递减。
- 所有会话默认存储在 `~/.camel/sessions/<session-id>.jsonl`；会话头、完整消息、压缩和元数据更新按类型追加到同一文件。
- 只有完整规范消息的 `message_end` 可触发消息持久化；Provider 增量、未完成 assistant 和临时审批事件不得写入会话日志。
- 原始消息、模型上下文和元数据由同一日志分别投影；不在运行时兼容旧格式。
- 新建会话目录和日志使用私有权限；损坏的单份日志不得阻断其他会话的列表读取。
- `ContextManager` 与 `CompactionEngine` 位于 `camel_harness.agent.context`，负责动态系统上下文和历史压缩；可依赖 ProjectRules、Skills、Memory 和只读 `SessionLog`，但不得持有 `SessionManager`、执行会话 CRUD 或读写 JSONL。
- Session 独立负责会话消息、元数据和压缩投影；Agent 通过有序事件把完整消息交给 Session，并协调其与 `ContextManager`，不处理 JSONL 格式。
- 规范消息属于 LLM，JSONL 消息编解码属于 Session，LLM 不感知持久化格式。
- 文件与 Shell 工具默认限制在工作区内；不得提交密钥或令牌。

## 前端、测试与文档

- WebUI 依赖方向为 `app -> features -> shared`，feature 之间禁止直接导入。
- 不得通过删除、跳过或弱化测试使构建通过。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 架构变化同步更新 `docs/项目架构.md`，计划变化同步更新 `docs/路书.md`。
- `docs/开发日志.md` 按自然日维护，同一天只有一个二级日期标题；每次开发记录主要变更和验证结果。

## git提交规范

- 使用中文提交描述，格式为 `<type>(<scope>): <subject>`，并按需填写正文和尾注。
