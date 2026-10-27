# 变更记录

## 1.0.1 — 2026-10-25

首个稳定版本，主要变化：

- **子任务可见性**：父会话汇总子任务进度与待审批数量，详情侧栏展示完整过程并直接处理审批；保存终态和结束原因，支持历史查看。

- **Web 入口**：移除 CLI/REPL、终端审批及 `camel-harness` 命令，仅保留 `camel-harness-web`；权限模式精简为 `default`、`plan`、`yolo`，删除 `accept_edits`。
- **长期记忆**：改用 `MEMORY.md` / `USER.md` 双文件和单个 `memory` 工具，支持条目增删改、字符上限和每 Turn 完整注入；除 Plan 模式外自动允许写入，提供旧 JSON 迁移脚本并更新配置字段。
- **上下文管理**：分离压缩决策、摘要执行与会话提交，统一 Token 估算和系统提示组装；主请求使用模型完整输出预算，保留压缩预留与超限恢复。
- **模型配置与恢复**：按模型管理窗口、输出预算和显式思考模式，支持旧配置迁移及设置页编辑；工具参数截断最多连续恢复两次，WebUI 区分输出、Turn 和上下文限制。
- **文件工具**：简化 `write_file` 为 `path/content`，移除版本校验；`read_file` 增加 16 KiB 正文上限与分页反馈；`edit_file` 支持批量精确替换，保留 BOM、未修改区域换行并返回 diff。
- **工具安全**：审批前固定参数，Shell 审批展示完整命令和实际工作目录；工具结果区分未执行、成功、失败与结果未知，无法确认副作用时提示核验，不自动重放。
- **内部重构**：拆分运行编排、记忆管理与存储职责，统一工具定义查询和执行、恢复状态、主 Agent 与子任务提示及依赖命名；保持会话持久化格式不变。
- **测试与文档**：精简重复测试，使用 Import Linter 检查依赖边界，保留关键故障回归；更新架构、路书与机制文档，移除独立评测及过时入口，私有学习材料移至 Git 忽略目录。

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
- 修复 Windows 旧版 pywin32 未导出 `FILE_FLAG_OPEN_REPARSE_POINT` 导致安全文件打开崩溃：按文档常量值 `0x00200000` 补齐。

架构与计划见 [项目架构](docs/项目架构.md) 与 [路书](docs/路书.md)。
