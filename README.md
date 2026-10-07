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
- 默认提供 read/write/edit/shell 四个核心工具、只读 `load_skill` 和 `delegate_task`；子 Agent 使用隔离会话和白名单工具集，不能递归委派；
- Skill 按名称与描述发现，正文和支持文件通过 `load_skill` 分层按需读取；Memory 由 `ContextManager` 检索后注入系统上下文；
- Plan、Accept Edits、Default 和 YOLO 权限模式；
- 浏览器异步审批与 CLI 审批；
- 每个会话目录内一份 `session.jsonl`；子会话位于父目录的 `subagents/` 下；
- 追加式上下文投影和历史压缩；
- Session 独立管理会话；`ContextManager` 统一提供压缩、ProjectRules、Skills 和 Memory 动态上下文；
- 一个 Agent 只绑定一个 Session 和 Workspace，运行期间支持 steering、主动停止和基于完整 JSONL 消息的恢复；
- WebUI 提供运行中追加、停止与继续，CLI 提供运行中输入、`/cancel` 和 `/resume`；
- SSE 仅在 Backend 传输层使用 `yield`，断开连接时显式中止对应 Run；
- FastAPI + React WebUI 与 Typer CLI。

## Skill 渐进式加载

内置 `explore-codebase`、`debug-issue`、`review-change` 和 `verify-change` 四个常用 Skill。项目级 Skill 放在当前工作区 `.camel/skills/<name>/SKILL.md`，用户级 Skill 放在 `~/.camel/skills/<name>/SKILL.md`；同名时优先级为内置 > 项目级 > 用户级。发现阶段只读取 frontmatter 的名称与描述；相关任务出现时，Agent 可调用只读 `load_skill(name)` 获取正文。正文引用的相对文件不会预先加载，需要时再调用 `load_skill(name, path)`，路径被限制在对应 Skill 目录内。

```text
.camel/skills/review/
├── SKILL.md
└── references/checklist.md
```

项目级和用户级 Skill 文件由用户直接维护；Harness 不自动评估、改写或回滚 Skill。

## 10 分钟快速启动

要求 Python 3.11+、Node.js 24 和 `uv`。本仓库锁定 Python 依赖与 WebUI npm 依赖；首次运行需要联网安装依赖，并准备对应 Provider 的密钥。

```bash
uv sync --locked --extra dev --python 3.11
npm ci --prefix webui
npm run build --prefix webui
# 首次启动会自动创建 ~/.camel/config.yaml。
uv run camel-harness-web
```

浏览器访问 `http://127.0.0.1:8000`。不想构建前端时，可在另一终端运行 `npm run dev --prefix webui`，由 Vite 开发服务器提供界面。CLI 使用 `uv run camel-harness "概括当前项目"`。已有会话可传 `--session ID`，或用 `--latest-session` 选择当前工作区最近的顶层会话；REPL 的 `/resume` 才是从完整消息边界续跑。

## 配置

CLI、Backend 和真实评测统一读取全局 `~/.camel/config.yaml`，不读取环境变量或项目 `.env`。完整字段见 [config.example.yaml](config.example.yaml)。文件缺失时自动创建默认配置，新建目录权限为 `0700`、文件为 `0600`；已有文件不覆盖。默认密钥为空，首次启动后可在 WebUI“设置 → 配置文件默认模型”填写。空文件使用默认值，未知字段和格式错误会明确报错。修改后重启入口进程生效，CLI 的 `--provider`、`--model` 可临时覆盖文件值。

```yaml
provider: anthropic
model: claude-sonnet-4-6
anthropic_api_key: "填写你的密钥"
anthropic_base_url: null
openai_api_key: null
openai_base_url: null
session_dir: ~/.camel/sessions
max_output_tokens: 4096
max_total_tokens: 200000
max_duration_seconds: 900
```

使用 OpenAI 时将 `provider` 改为 `openai`，填写对应的 `model` 和 `openai_api_key`。相对 `session_dir` 固定相对于用户主目录解析；`mcp_config` 仍相对于 Agent 工作区。配置文件含密钥，请保持 `0600` 权限。

WebUI 左下角“设置”可添加多个 Anthropic/OpenAI 模型配置并保存到本机 `~/.camel/web-settings.json`（自定义 `session_dir` 时位于其父目录），文件权限为 `0600`。浏览器接口不会返回已保存的 API Key；自定义模型留空密钥时使用 YAML 中对应 Provider 的密钥。会话选择从下一次 Run 生效。

在“设置 → 配置文件默认模型”中，通过表单选择 Provider、填写模型 ID、API Key 和自定义接口地址，直接保存到全局 `config.yaml`。密钥不回显，留空保留旧值，勾选“清除”才会删除。预算、会话目录及另一厂商配置保持原值。保存后重启后端使 WebUI 生效，CLI 下次启动时读取；文件被其他编辑器改动后须重新加载。

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
~/.camel/sessions/<session-id>/session.jsonl
~/.camel/sessions/<session-id>/subagents/<child-id>/session.jsonl
```

- 首行 `session` 保存会话 ID、工作目录及初始元数据；其后按顺序追加 `message`、`compaction` 和 `session_info`。
- `message` 只保存完整的 user、assistant 和 toolResult；压缩不删除原始消息，模型上下文由日志重放得到。
- 孙会话继续放在其父会话目录的 `subagents/` 下；删除父会话时级联删除后代会话日志。
- 超大工具结果存放在对应会话目录的 `tool-results/` 下，消息保留预览和完整文件的绝对路径；删除会话时一并清理结果文件。工作区文件工具不因这些引用而获得目录外的访问权限。
- 旧的平铺 JSONL 不会在运行时自动读取；迁移时应保留原文件作备份。

## 质量检查

```bash
uv run ruff format --check src tests evals scripts
uv run ruff check src tests evals scripts
uv run mypy src
uv run pytest -q
uv run lint-imports
npm test --prefix webui
npm run build --prefix webui
uv run python -m evals.run
uv build
```

离线评测的 11 个任务使用脚本化假 Adapter，只验证 Harness 的读写、编辑、测试、错误和恢复链路，不是模型成功率。真实模型基线需要显式运行：

```bash
uv run python -m evals.run --provider anthropic --model 你的模型ID --output baseline.json
uv run python -m evals.provider_smoke --provider anthropic --model 你的模型ID
# OpenAI 同理改成 --provider openai，并在 YAML 中配置 openai_api_key。
```

当前已记录的真实协议兼容端点基线与失败样本见 [评测说明](evals/README.md)。运行真实评测可能产生 API 费用。评测只在临时工作区写文件，工具审批只允许文件编辑与固定的 `python -m pytest -q`；这不是 OS 沙箱。

## 旧会话迁移

停止 CLI/Web 后先备份整个 `~/.camel/sessions`，再预览、执行一次性复制迁移：

```bash
cp -a ~/.camel/sessions ~/.camel/sessions-backup-2026-10-07
uv run python scripts/migrate_flat_sessions.py
uv run python scripts/migrate_flat_sessions.py --apply
```

脚本依据旧日志首行的 `parentSessionId` 安排子目录，逐个比较 SHA-256；遇到目标内容冲突会停止，不覆盖、不删除旧平铺文件。确认新会话都可读取后，再自行决定何时清理原文件。本项目不会在运行时读取旧平铺布局。

## 3 分钟演示与限制

1. 在 WebUI 选择测试工作区，发送“读取一个文件并概括”，观察文本增量与内联工具轨迹。
2. 发送“新建 demo.txt”，在默认权限下观察审批并批准；再次发送长任务，点击停止，再在界面选择继续。
3. 发送“委派 explore 子 Agent 只读检查工作区”，检查 `~/.camel/sessions/<父会话>/subagents/<子会话>/session.jsonl`。

本地 WebUI 已做浏览器画面核对；截图产物尚未纳入仓库。支持单机、同页多个会话并行，切换会话保留原会话执行；每个 Agent 仍只有一个活动 Run。刷新或关闭页面会取消连接对应的 Run，SSE 不回放；同工作区并行会话共享文件，没有自动隔离。没有进程级沙箱和副作用工具 exactly-once 保证，也不承诺多用户或后台任务接管。OpenAI 真实烟测入口已实现，但当前基线环境没有 OpenAI 凭据，尚无真实验证结果。更多边界见[项目架构](docs/项目架构.md)。
