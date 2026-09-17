# Camel Harness 项目约束

本文件对整个仓库生效。项目采用 pi-agent 风格的功能模块架构，不再使用 DDD、限界上下文和 application/domain/infra 分层。

## 语言与注释

- 源代码标识符使用英文。
- 代码注释、docstring、错误说明和项目文档默认使用中文。
- 注释解释设计原因、边界条件和不变量，不逐行翻译代码。
- 外部协议字段、标准错误码和行业固定术语保留英文。
- 公共类型、核心循环、存储接口和复杂策略必须提供中文 docstring。

## 后端结构

后端按运行职责组织：

```text
backend/
├── agent/        # Agent 状态、事件与低层循环
├── compaction/   # 上下文组装与压缩
├── llm/          # 统一模型协议和 Provider
├── session/      # 会话状态与 JSONL 存储
├── tools/        # 工具、权限、插件和 MCP
├── resources/    # Skill、Memory 和项目规则
├── types/        # Message、Tool 等稳定公共类型
├── api/          # FastAPI、SSE 和请求模型
├── harness.py    # 产品级编排门面
└── runtime.py    # 唯一运行时组合模块
```

依赖方向：

```text
API / CLI -> Harness -> Agent -> AgentLoop
                  |         |-> LLM
                  |         +-> Tools
                  +-> Session / Compaction / Resources

Runtime -> 所有需要装配的功能模块
```

约束：

1. 禁止恢复 `core/application/domain/infra/bootstrap` 分层。
2. 功能模块内禁止为了形式创建 `models/`、`services/`、`ports/` 目录。
3. `backend.types` 只能依赖 Python 标准库。
4. `AgentLoop` 只协调 LLM、工具、预算和流事件，不读取文件、访问 Session 或构建项目资源。
5. `Agent` 保存默认配置并驱动 `AgentLoop`，不得接管 HTTP 或 JSONL 格式。
6. `Harness` 负责会话、上下文压缩、资源加载和消息保存点，是 CLI 与 API 的主要入口。
7. `runtime.py` 是唯一完整组合模块；禁止恢复按领域拆分的 wiring 文件。
8. 功能模块不得依赖 `backend.api`；HTTP 路由只做请求校验、状态码和输出投影。
9. 只有存在真实多实现或扩展需求时才能新增 ABC。单实现策略优先使用具体类或 callable。
10. 不保留旧 Python 导入路径、转发模块或兼容壳。

## WebUI

- React 代码使用 `webui/src/app`、`webui/src/features`、`webui/src/shared` 三层，依赖方向为 `app -> features -> shared`。
- feature 之间禁止直接导入，通过 app 传递稳定数据或回调。
- 当前规模不引入 Redux、事件总线或前端 DDD。
- API 路由集中在 `backend.api.routes`，请求模型放在 `backend.api.contracts`。

## LLM 与流式事件

- 默认 Provider 为 Anthropic，同时支持 OpenAI。
- OpenAI 与 Anthropic 实现必须使用官方 Python SDK。
- 厂商请求、响应和 SDK 类型只能存在于 `backend.llm`。
- Provider 必须把厂商流转换为统一 `LlmStreamEvent`，未知内容不得静默丢弃。
- 流事件必须包含 `session_id`、`turn_id`、`step_id` 和单调递增序号。
- 文本增量实时转发；只有模型流正常结束后才能提交完整 assistant 消息。
- `LlmClient` 只在首个事件发出前重试，避免消费者收到重复增量。

## Agent 与上下文

- `AgentLoop` 维护 Turn/Step、工具循环、停止原因和预算。
- `AgentOptions` 统一承载 Provider、Model、输出限制、Step 限制、总 token、总时长和 thinking 开关。
- 所有耗时操作必须支持超时，可取消操作必须传播取消。
- 上下文构建与压缩位于 `backend.compaction`；压缩结果通过 Harness 写入 Session。
- 子 Agent 使用隔离 Session、独立预算和显式工具白名单。

## 工具系统

- `Tool` ABC、`ToolContext`、`ToolDefinition`、`ToolCall` 和 `ToolResult` 必须位于 `backend.types.tool`。
- 工具直接声明 `name`、`description`、`parameters` 等字段并实现 `execute()`。
- 新工具不得要求修改 Agent Loop。
- 动态工具通过 `ToolProvider` 注册，名称冲突不得静默覆盖。
- 工具执行依次经过参数验证、权限、审批、超时、结果归一化和审计。
- MCP 使用官方 SDK，并由 Harness 管理连接生命周期。
- 大型结果落盘并返回引用，禁止无限制写入模型上下文。

## 会话存储

- 所有会话默认存储在用户级 `~/.camel/sessions/`，不得写入当前项目。
- 每个会话目录包含 `messages.jsonl`、`context.jsonl` 和 `metadata.json`。
- `messages.jsonl` 只保存完整规范消息，不保存 chunk、Turn/Step 生命周期或重复响应。
- `context.jsonl` 的 Append 只引用消息下标；Replace 保存压缩摘要及遮蔽区间。
- UI 历史读取完整消息，模型上下文重放 Append/Replace，不得恢复时重新压缩。
- 不兼容旧会话格式，不增加迁移探测分支。

## 测试与质量

- Agent Loop 优先使用内存回调和假 Provider 做纯单元测试。
- 每个 LLM Provider 通过同一组契约测试。
- 必须测试流事件顺序、ToolCall/ToolResult 配对、会话恢复、取消、审批和权限绕过。
- 架构测试检查功能模块、Tool 类型位置和 AgentLoop 边界，不检查 DDD 目录。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 不得删除、跳过或弱化测试来使构建通过。

## 文档与日志

- 架构变化同步更新 `docs/项目架构.md`。
- 计划和里程碑变化同步更新 `docs/路书.md`。
- `docs/开发日志.md` 以当前扁平架构版本为历史基线，此后按自然日只追加。
- 同一天只有一个二级日期标题；记录主要变更、关键决策、验证结果和后续事项。

## 安全

- 不得在代码、日志、测试快照或文档中提交密钥和令牌。
- 文件和 Shell 工具默认限制在工作区内。
- 写文件、执行命令和外部访问必须经过权限策略。
- Skill 自动演化默认关闭；启用前必须具备确认、审计、版本快照和回滚。
