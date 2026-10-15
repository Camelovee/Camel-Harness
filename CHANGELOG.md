# 变更记录

## 0.1.0 — 2026-10-15

- Python Harness Agent：流式多 Turn ReAct、生命周期事件、steering、取消与恢复。
- LLM 层无状态化：凭据随 `LlmRequest` 的 `LlmModel` 进入，官方 SDK Client 每次流式调用内创建关闭；`LlmClient` 按 Provider 路由多适配器。
- Anthropic 和 OpenAI 官方 SDK 适配，支持工具调用、思考增量与用量事件；思考强度为 per-run 参数（低/中/高，映射 `output_config.effort` / reasoning effort）。
- 模型配置统一在 `~/.camel/config.json` 的 `models` 列表，Web 设置保存即生效；模型选择是 `run()`/`resume()` 的必填参数，运行中切换无需重建 Agent。
- 可选能力（Skill、Memory、MCP、子 Agent 委派）经 `AgentExtensions` 由入口层注入，`runtime.py` 只负责组装；子 Agent 设计收敛为 `agent/subagent` 包并跟随父 Run 模型。
- 四个核心工具、权限审批、MCP、Skill 渐进式加载和子 Agent 委派；长期记忆双层检索与审批维护。
- 本地 JSONL 会话、父子会话目录、上下文压缩及溢出恢复；工作区即主目录时记忆层级回落，不再拒绝启动。
- WebUI 设置对话框改左右结构（模型 / Skills / MCP 预留），Composer 支持模型、思考强度与权限模式选择；CLI `--model` 按名称或 ID 匹配配置。
- 新增锁定依赖的 CI、离线评测、真实 Provider 可选烟测与任务报告。
- 提供旧平铺 JSONL 的预览式复制迁移脚本；项目采用 MIT 许可证。

架构与计划见 [项目架构](docs/项目架构.md) 与 [路书](docs/路书.md)。
