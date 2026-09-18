# Camel Harness 项目约束

本文件对整个仓库生效，只记录不可违反的项目约束。

## 代码规范

- 标识符使用英文；代码注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界条件和不变量，不逐行翻译代码。
- 保持实现简洁；只有存在真实多实现或扩展需求时才新增 ABC、转发层或目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

## 核心边界

```text
Backend / CLI -> Agent -> agent_loop() -> LLM
                   |          +--------> Tools
                   +-> Session / Compaction / Resources
```

- Python 源码分为 `camel_harness`、`camel_backend` 和 `camel_cli` 三个根包；Backend 与 CLI 互不依赖。
- `camel_harness` 只包含 `agent` 和 `llm` 两个功能包；依赖只能沿 `camel_backend/camel_cli -> camel_harness.agent -> camel_harness.llm` 流动。
- `camel_harness.llm` 拥有规范消息、模型请求、工具定义和流事件，不得依赖 `agent`、Backend 或 CLI。
- LLM 请求和事件不得包含 `session_id`、`turn_id`、`step_id` 或 Agent 事件序号；这些运行坐标只能由 `agent_loop()` 注入 `AgentStreamEvent`。
- `agent_loop()` 是无状态异步生成器，只负责模型、工具、预算和流事件，不访问 Session、文件系统、HTTP 或资源加载；不得恢复为类。
- `Agent` 负责会话、上下文压缩、资源加载和完整消息保存点，是 API 与 CLI 的主要入口。
- `Agent` 的公共方法只保留 `run_turn()` 和 `close()`；Session CRUD、工作区选择、审批查询和扩展诊断由入口层持有。
- `camel_harness.agent.runtime` 是唯一完整组合模块；Agent 不得依赖 Backend 或 CLI。
- HTTP 路由只做请求校验、状态码和输出投影。

## LLM、工具与会话

- 默认 Provider 为 Anthropic，同时支持 OpenAI；两者必须使用官方 Python SDK。
- 厂商对象只能存在于 `camel_harness.llm`，对外统一为 `LlmStreamEvent`。
- 规范消息必须使用 `Message = UserMessage | AssistantMessage | ToolResultMessage`；系统提示通过 `LlmRequest.system_prompt` 单独传递，不得恢复通用 `role/content` 消息容器。
- `AssistantMessage.stop_reason` 必须使用厂商无关的 `LlmStopReason`；它与 Agent Turn 级 `StopReason` 是两个独立概念。
- `length` 结束的工具调用禁止执行，必须写入错误工具结果以保持消息链完整。
- 模型增量实时转发；只有流正常结束后才能保存完整 assistant 消息，禁止保存 chunk。
- `Tool` ABC 及 `ToolContext`、`ToolCall`、`ToolResult` 位于 `camel_harness.agent.tools.types`；给模型使用的 `ToolDefinition` 位于 `camel_harness.llm`，只包含最终工具名、描述和输入 Schema，不感知 namespace。
- 新工具通过 `ToolProvider` 注册，不得修改 Agent Loop；工具执行必须经过参数校验、权限、审批、超时和结果归一化。
- 所有会话默认存储在 `~/.camel/sessions/<session-id>/`，包含 `metadata.json`、`messages.jsonl` 和 `context.jsonl`。
- `messages.jsonl` 只保存完整规范消息；不兼容旧会话格式。
- 规范消息属于 LLM，JSONL 消息编解码属于 Session，LLM 不感知持久化格式。
- 文件与 Shell 工具默认限制在工作区内；不得提交密钥或令牌。

## 前端、测试与文档

- WebUI 依赖方向为 `app -> features -> shared`，feature 之间禁止直接导入。
- 不得通过删除、跳过或弱化测试使构建通过。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 架构变化同步更新 `docs/项目架构.md`，计划变化同步更新 `docs/路书.md`。
- `docs/开发日志.md` 按自然日维护，同一天只有一个二级日期标题；每次开发记录主要变更和验证结果。
