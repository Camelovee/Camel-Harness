# Camel Harness 项目约束

本文件对整个仓库生效，只保留代码规范和基本项目约束；实现细节放在代码、测试及架构文档中。

## 代码规范

- 标识符使用英文；注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界和不变量，不逐行翻译代码。
- 保持实现简洁，避免过度抽象、重复职责和无需求的目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

## 基本项目约束

- Python 分为 `camel_harness`、`camel_backend` 两个根包；WebUI 通过 Backend 使用 Harness。
- Harness 分为 `agent` 与 `llm`；依赖方向为 `Backend -> Agent -> LLM`，禁止反向依赖。
- Agent 负责运行编排，LLM 负责厂商适配；会话、上下文和工具各自承担独立职责，不混入入口层逻辑。
- 一个 Agent 绑定一个会话和工作区，同一时间只运行一个任务；运行与事件观察分离。
- WebUI 依赖方向为 `app -> features -> shared`，feature 之间禁止直接导入。
- 默认使用 Anthropic，同时支持 OpenAI；厂商适配使用官方 Python SDK。
- 配置统一使用 `~/.camel/config.json`，不读取环境变量或项目 `.env`；会话使用全局本地 JSONL 存储，不引入数据库。
- 遵守工作区和权限边界；敏感操作经过审批，不自动重试已产生副作用的操作。
- 保证流式输出、中断恢复和持久化的一致性；不保存未完成的模型消息，不将存储失败伪装成工具未执行。
- 明确资源所有权，异常和取消时清理资源，不关闭借用资源。
- 不提交密钥、令牌、真实会话或其他用户私有数据；错误和日志不得泄漏密钥。

## 测试与文档

- 不得通过删除、跳过或弱化测试使构建通过。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 版本更新在 `CHANGELOG.md` 中按版本号记录变更；架构变化更新 `docs/项目架构.md`，计划变化更新 `docs/路书.md`；方案设计放在 `docs/方案设计/`。

## Git 提交

- 使用中文提交描述，格式为 `<type>(<scope>): <subject>`。
- 保留用户已有改动，不擅自覆盖、删除或提交与当前任务无关的内容。
- 不要私自提交，用户让你提交再提交

## MCP 集成

- RepoAtlas 是可选 MCP 集成（`mcp__repoatlas__*` 工具），用于代码库检索 / Wiki / 问答。公开配置见 `.mcp.example.json`，本机配置保存在不纳入 Git 的 `.mcp.json`；复现步骤见 `docs/repoatlas-mcp-demo.md`。
- `McpManager` 强制 MCP server 的 `cwd = workspace`；通过 `--project <RepoAtlas路径>` 指定本机项目目录，必要时使用 `uv` 的绝对路径，不依赖 `cwd` 字段。
