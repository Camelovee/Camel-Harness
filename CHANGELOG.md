# 变更记录

## 未发布

- 在 Context 模块集中维护主 Agent 默认提示和 explore/plan/general 角色提示，明确任务边界、证据定位、重复调用、验证及交付要求；保留自定义基础提示与动态资源组装行为。
- Agent 的工具定义查询与执行统一经 ToolExecutor，移除重复注册表注入；输出截断和上下文超限的独立恢复状态统一由 AgentRun 持有。
- 基础系统提示由 ContextManager 持有、校验及组装，Agent 仅消费准备结果；子任务使用独立角色上下文，共享压缩引擎及动态资源，不修改父实例。
- 统一 Python 源码、脚本和测试的依赖及局部变量命名，明确管理器、客户端、配置、请求和集合的职责；长依赖组装改用关键字参数，对外协议与持久化格式保持不变。
- 移除项目评测任务集、运行脚本、历史基线与本机报告，并清理相关测试、CI 入口及文档引用。
- 移除 CLI/REPL、终端审批和 `camel-harness` 命令，仅保留 `camel-harness-web` Web 入口；删除 Typer 直接依赖和 CLI 打包配置。
- 精简只检查代码形状及重复行为的回归测试，Python 依赖边界统一交由 Import Linter 检查；配置路径与 Shell 测试归并到对应模块，保留取消、持久化、权限与资源清理等故障回归。
- 按当前 Web 入口重写架构和路书，补充上下文管理、工具安全、会话、上下文压缩与记忆机制说明，删除独立 WebUI 设计文档。
- 个人简历模板与面试准备移至本机 `docs/学习/`，整个目录由 Git 忽略。

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
