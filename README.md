# Camel Harness

Camel Harness 是一个受 pi-agent 启发的 Python Agent Runtime。它提供流式 ReAct 循环、OpenAI/Anthropic Provider、精简可扩展工具、子 Agent 委派、权限审批、MCP、动态上下文、压缩和纯本地 JSONL 会话。

项目不使用 DDD 和多层 Port/Service 结构，而是将 Agent、LLM 与两种入口按依赖方向组织：

- `llm`：规范消息、请求、流事件，以及 OpenAI/Anthropic 适配；
- `Agent`：统一编排 Run/Turn 生命周期、LLM、工具、会话、上下文和持久化；
- `Backend / CLI`：只提供 HTTP/SSE 或终端入口。

## 架构

```text
Backend / CLI -> Agent -> LLM
                   +----> Tools
                   +----> Session / Context / Resources
```

```text
src/
├── camel_harness/
│   ├── llm/             # 独立模型协议与厂商适配
│   └── agent/           # 完整 Agent、循环、工具与产品能力
├── camel_backend/       # FastAPI、SSE 与浏览器审批
└── camel_cli/           # Typer、终端渲染与命令行审批
```

详细设计见 [项目架构](docs/项目架构.md)，开发计划见 [路书](docs/路书.md)。

## 已实现能力

- 默认 Anthropic Provider，可切换 OpenAI；两者均使用官方 SDK；
- 使用 `UserMessage | AssistantMessage | ToolResultMessage` 判别联合，系统提示与会话消息分离；
- Assistant 消息保留规范化 `stop/length/tool_use` 结束原因，截断的工具参数不会被执行；
- `AgentEvent` 判别联合完整描述 Agent、Turn、Message、Tool 和 Approval 生命周期；
- Agent 自己持有单一 Run 任务，WebUI、CLI 和子 Agent 通过轻量 `subscribe()` 观察事件；
- 文本、thinking、usage 和工具调用增量通过 `message_update.assistant_event` 实时转发；
- LLM 协议不携带 Session、Run、Turn 等 Agent 运行坐标，由 `Agent` 在公开事件中统一补齐；
- 事件 `sequence` 在整个 Run 内单调递增，可用于 SSE 排序和去重；
- 流正常结束后批量追加完整 assistant 消息，不保存 chunk；
- `Tool` ABC 直接字段式定义，支持显式注册和 MCP；
- 默认提供 read/write/edit/shell 四个核心工具与 `delegate_task`；子 Agent 使用隔离会话和白名单工具集，不能递归委派；
- Skill 和 Memory 由 `ContextManager` 检索后注入系统上下文；
- Plan、Accept Edits、Default 和 YOLO 权限模式；
- 浏览器异步审批与 CLI 审批；
- `~/.camel/sessions/<session-id>/` 下的 JSONL 会话恢复；
- 追加式上下文投影和历史压缩；
- Session 独立管理会话；`ContextManager` 统一提供压缩、ProjectRules、Skills 和 Memory 动态上下文；
- 一个 Agent 只绑定一个 Session 和 Workspace，运行期间支持 steering、主动停止和基于完整 JSONL 消息的恢复；
- WebUI 提供运行中追加、停止与继续，CLI 提供运行中输入、`/cancel` 和 `/resume`；
- SSE 仅在 Backend 传输层使用 `yield`，断开连接时显式中止对应 Run；
- FastAPI + React WebUI 与 Typer CLI。

## 安装

要求 Python 3.11+、Node.js 和 `uv`。

```bash
uv sync --extra dev
cd webui
npm install
```

## 配置

默认使用 Anthropic：

```bash
export ANTHROPIC_API_KEY="..."
```

使用 OpenAI：

```bash
export CAMEL_PROVIDER="openai"
export OPENAI_API_KEY="..."
export CAMEL_MODEL="gpt-5"
```

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `CAMEL_PROVIDER` | `anthropic` | `anthropic` 或 `openai` |
| `CAMEL_MODEL` | `claude-sonnet-4-6` | 模型名称 |
| `CAMEL_MAX_OUTPUT_TOKENS` | `4096` | 单次模型最大输出 |
| `CAMEL_MAX_TOTAL_TOKENS` | `200000` | 单次 Run 总预算 |
| `CAMEL_MAX_DURATION_SECONDS` | `900` | 单次 Run 总时限 |
| `CAMEL_SESSION_DIR` | `~/.camel/sessions` | 全局会话目录 |

WebUI 左下角“设置”可添加多个 Anthropic/OpenAI 模型配置，填写模型 ID、API Key 和可选接口地址，并设置默认模型与思考模式。对话输入框可为当前会话选择模型，选择保存在该会话元数据中；切换模型从下一次 Run 生效。自定义配置保存在本机 `~/.camel/web-settings.json`（若自定义了 `CAMEL_SESSION_DIR`，则位于其父目录），文件权限为 `0600`。浏览器接口不会返回已保存的 API Key；留空密钥时使用对应 Provider 的环境变量。

## 运行

单次 CLI：

```bash
uv run camel-harness "读取当前项目并总结"
```

交互模式：

```bash
uv run camel-harness
```

启动 Web：

```bash
uv run camel-harness-web
```

WebUI 开发服务器：

```bash
cd webui
npm run dev
```

## Tool 定义

`Tool` 位于 `camel_harness.agent.tools`，具体工具也按功能放在该包中：

```python
class ExampleTool(Tool):
    namespace = "example"
    name = "echo"
    label = "回显"
    description = "返回输入文本"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        return ToolResult(call.call_id, call.name, str(call.arguments["text"]))
```

新增工具不需要修改 Agent 主流程，只需通过 `ToolProvider` 显式注册。默认运行时不扫描 Python Entry Point，避免工具集在不可见的情况下膨胀；外部能力通过 `.mcp.json` 按项目接入。

## 会话格式

```text
~/.camel/sessions/<session-id>.jsonl
```

- 首行 `session` 保存会话 ID、工作目录及初始元数据；其后按顺序追加 `message`、`compaction` 和 `session_info`。
- `message` 只保存完整的 user、assistant 和 toolResult；压缩不删除原始消息，模型上下文由日志重放得到。
- 旧格式不会在运行时自动读取。

## 质量检查

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest -q
uv run lint-imports
cd webui && npm run build
```
