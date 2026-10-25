# 变更记录

## 1.0.1 — 2026-10-25

- 发布首个稳定版本：完成上下文压缩、Hermes 风格长期记忆、模块分层和 Agent 编排拆分，全量回归通过。

- 抽出 `TurnRunner` 与 `SessionCommitter`：TurnRunner 负责单 Turn 的上下文准备、模型流、溢出重试与工具分发，SessionCommitter 统一“先保存消息再发确认”的顺序和 `SESSION_FAILED` 分类；Agent 只保留公开生命周期、Run 循环、取消/steering 与唯一终态。运行行为、事件顺序和持久化格式保持不变。

- 拆分长期记忆为 `agent/memory/` 包：`MemoryManager` 负责开关、校验、上限、匹配与提示渲染，`MemoryStore` 负责路径、锁、解析与原子提交，实体和错误集中在 `types.py`；写入通过锁内事务回调保持原子性，存储层不再依赖 `MemorySettings`。会话包把编解码并入 `events.py`，`SessionMetadata` 与 `SCHEMA_VERSION` 下沉到 `types.py`，并新增对应的 Import Linter 契约。对外行为、记忆文件格式和会话 JSONL 格式保持不变。

- 按 Hermes 方式改造长期记忆：使用 MEMORY.md / USER.md 两个有界文件（默认 2200 / 1375 字符）、单个 `memory` 工具以 add/replace/remove 和唯一子串维护条目，并在每个 Turn 完整注入系统提示。记忆写入在 PLAN 外自动允许，不再逐次审批；新增 `scripts/migrate_memory.py` 迁移旧 JSON 条目。原检索、分页读取、版本校验和审批接口移除，配置字段改为 `user_profile_enabled`、`memory_char_limit`、`user_char_limit`。

- 分离上下文压缩决策与执行：ContextManager 依据压缩开关、最少消息、压力阈值和容量保护判断是否需要压缩；CompactionEngine 只负责选择安全切点、生成摘要并校验压缩结果，强制恢复仍要求历史严格缩小。压缩配置、错误分类、会话提交和持久化格式保持不变。

- 上下文准备流程由 ContextManager 按次借用 Session 完成：读取快照、组装提示、生成并提交压缩计划，成功后返回系统提示和消息。Agent 只接入用量统计和故障分类；Session 继续负责存储一致性与在途取消。依赖约束允许公开 Session 接口，禁止直接操作存储及依赖运行状态。

- 简化主请求预算职责：上下文准备仅返回系统提示和历史，主请求直接使用模型配置的完整输出预算，超限恢复保持相同额度；Context 保留容量检查与压缩预留，摘要仍独立夹取首次／重试预算。

- 模型配置集中维护窗口、输出预算和显式思考模式；旧全局字段读取时迁移，设置页支持编辑。主请求、子任务及摘要共用模型能力和 4096 Token 安全余量，压缩预留覆盖输出预算；默认省略未经确认的思考参数。工具参数截断最多连续恢复两次，正常响应清零，纯文本截断不自动续写；终态诊断与 WebUI 区分输出、Turn 和上下文限制。

- 参考 pi 简化文件工具：write_file 仅保留 path/content，移除文件版本校验；read_file 增加 16 KiB 正文上限和完整分页反馈；edit_file 改为原始快照上的批量精确替换，保留 BOM 与未修改区域换行并返回 diff。主 Agent 和 general 角色补充简洁文件操作指引，保留审批、路径保护、原子提交及未知结果核验规则。
- Shell 审批展示实际工作目录和完整执行命令，调用参数在审批前固定；曾加入文件覆盖版本校验，现由上述简单写入接口替代。工具结果区分未执行、成功、失败和结果未知，超时或无法确认副作用时保留核验提示及前端标签，不自动重放。
- 统一 Context 的 Token 计算入口，优先使用匹配当前 Provider/模型的最近有效 usage 加尾部消息估算；缺失时全文估算，压缩后通过内存快照边界排除旧 usage。Memory、工具开销、近期历史及摘要输入复用估算函数，JSONL 格式与累计用量审计保持不变。
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
