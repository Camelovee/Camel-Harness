# Camel Harness

Camel Harness 是一个受 pi-agent 启发的 Python Agent Runtime。它提供流式 ReAct 循环、OpenAI/Anthropic Provider、可扩展工具、权限审批、MCP、Skill、Memory、子 Agent、上下文压缩和纯本地 JSONL 会话。

项目不再使用 DDD 和多层 Port/Service 结构，而是按可复用 Agent、产品 Harness 和两种入口组织：

- `AgentLoop`：只处理 LLM、工具和流事件；
- `Agent`：保存默认模型与预算配置；
- `Harness`：协调会话、资源、压缩、持久化和入口生命周期。

## 架构

```text
Backend / CLI -> Harness -> Agent -> AgentLoop
                       |         |-> LLM
                       |         +-> Tools
                       +-> Session / Compaction / Resources
```

```text
src/camel_harness/
├── agent.py             # 有状态 Agent 门面
├── agent_loop.py        # 流式 ReAct 核心循环
├── llm/                 # 官方 SDK Provider 与规范事件
├── types/               # Message、Tool ABC 和稳定类型
├── tools/               # 工具注册、执行和权限核心
├── harness/             # 会话、压缩、资源、产品工具和编排
├── backend/             # FastAPI、SSE 与浏览器审批
└── cli/                 # Typer、终端渲染与命令行审批
```

详细设计见 [项目架构](docs/项目架构.md)，开发计划见 [路书](docs/路书.md)。

## 已实现能力

- 默认 Anthropic Provider，可切换 OpenAI；两者均使用官方 SDK；
- 文本、thinking 和工具调用的实时流式事件；
- 流正常结束后批量追加完整 assistant 消息，不保存 chunk；
- `Tool` ABC 直接字段式定义，支持内置工具、Python Entry Point 和 MCP；
- 文件、Shell、Skill、Memory、工具搜索和子 Agent 工具；
- Plan、Accept Edits、Default 和 YOLO 权限模式；
- 浏览器异步审批与 CLI 审批；
- `~/.camel/sessions/<session-id>/` 下的 JSONL 会话恢复；
- 追加式上下文投影和历史压缩；
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
| `CAMEL_MAX_TOTAL_TOKENS` | `200000` | 单 Turn 总预算 |
| `CAMEL_MAX_DURATION_SECONDS` | `900` | 单 Turn 总时限 |
| `CAMEL_SESSION_DIR` | `~/.camel/sessions` | 全局会话目录 |

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

`Tool` 位于 `camel_harness.types.tool`，具体产品工具放在 `camel_harness.harness.tools`：

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

新增工具不需要修改 Agent Loop，只需通过 `ToolProvider` 注册。

## 会话格式

```text
~/.camel/sessions/<session-id>/
├── metadata.json
├── messages.jsonl
└── context.jsonl
```

- `messages.jsonl`：完整 user、assistant 和 tool 消息；
- `context.jsonl`：Append/Replace 上下文投影；
- `metadata.json`：名称、工作目录、时间、消息数和归档状态。

## 质量检查

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src/camel_harness
uv run pytest -q
uv run lint-imports
cd webui && npm run build
```
