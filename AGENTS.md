# Camel Harness 项目约束

本文件对整个仓库生效，只记录不可违反的项目约束。

## 代码规范

- 标识符使用英文；代码注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界条件和不变量，不逐行翻译代码。
- 保持实现简洁；只有存在真实多实现或扩展需求时才新增 ABC、转发层或目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

## 核心边界

```text
Backend / CLI -> Harness -> Agent -> AgentLoop
                       |         |-> LLM
                       |         +-> Tools
                       +-> Session / Compaction / Resources

Harness Runtime -> 所有需要装配的模块
```

- Python 源码使用 `src/camel_harness/` 布局；发行名为 `camel-harness`，导入名为 `camel_harness`。
- Agent Core 位于包根，依赖只能沿 `backend/cli -> harness -> core` 流动；`backend` 与 `cli` 互不依赖。
- `camel_harness.types` 只保存稳定公共类型，并且只能依赖 Python 标准库。
- `AgentLoop` 只负责模型、工具、预算和流事件，不访问 Session、文件系统、HTTP 或资源加载。
- `Harness` 负责会话、上下文压缩、资源加载和完整消息保存点，是 API 与 CLI 的主要入口。
- `camel_harness.harness.runtime` 是唯一完整组合模块；Harness 不得依赖 Backend 或 CLI。
- HTTP 路由只做请求校验、状态码和输出投影。

## LLM、工具与会话

- 默认 Provider 为 Anthropic，同时支持 OpenAI；两者必须使用官方 Python SDK。
- 厂商对象只能存在于 `camel_harness.llm`，对外统一为 `LlmStreamEvent`。
- 模型增量实时转发；只有流正常结束后才能保存完整 assistant 消息，禁止保存 chunk。
- `Tool` ABC 及 `ToolContext`、`ToolCall`、`ToolResult` 位于 `camel_harness.types.tool`。
- 新工具通过 `ToolProvider` 注册，不得修改 Agent Loop；工具执行必须经过参数校验、权限、审批、超时和结果归一化。
- 所有会话默认存储在 `~/.camel/sessions/<session-id>/`，包含 `metadata.json`、`messages.jsonl` 和 `context.jsonl`。
- `messages.jsonl` 只保存完整规范消息；不兼容旧会话格式。
- 文件与 Shell 工具默认限制在工作区内；不得提交密钥或令牌。

## 前端、测试与文档

- WebUI 依赖方向为 `app -> features -> shared`，feature 之间禁止直接导入。
- 不得通过删除、跳过或弱化测试使构建通过。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 架构变化同步更新 `docs/项目架构.md`，计划变化同步更新 `docs/路书.md`。
- `docs/开发日志.md` 按自然日维护，同一天只有一个二级日期标题；每次开发记录主要变更和验证结果。
