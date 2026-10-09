# Camel Harness 项目约束

本文件对整个仓库生效，只记录不可违反的项目约束。

## 代码规范

- 标识符使用英文；代码注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界条件和不变量，不逐行翻译代码。
- 保持实现简洁；只有存在真实多实现或扩展需求时才新增 ABC、转发层或目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

## 核心边界

```text
Backend / CLI -> Agent -> LLM
                   +----> Tools
                   +----> Session
                   +----> ContextManager -> Compaction / Resources
```

- Python 源码分为 `camel_harness`、`camel_backend` 和 `camel_cli` 三个根包；Backend 与 CLI 互不依赖。
- `camel_harness` 只包含 `agent` 和 `llm` 两个功能包；依赖只能沿 `camel_backend/camel_cli -> camel_harness.agent -> camel_harness.llm` 流动。
- `camel_harness.llm` 拥有规范消息、模型请求、工具定义和流事件，不得依赖 `agent`、Backend 或 CLI。
- LLM 请求和事件不得包含 `session_id`、`run_id`、`turn_id` 或 Agent 事件序号；这些运行坐标由 `Agent` 注入 `AgentEvent`。
- 不单独建立 `agent_loop` 模块或对外函数；`Agent` 内部完整编排会话、ReAct Turn、上下文压缩、资源加载和完整消息保存点。
- 一个 `Agent` 实例只绑定一个 Session 和 Workspace，同时最多运行一个 Run；Backend 只能在入口层按 `session_id` 定位 Agent，不得让 Agent 管理 Session 字典。
- Agent 只持有绑定单个已存在会话的 `Session`，不得持有 `SessionManager`；SessionManager 在入口/组合层管理会话 CRUD 与父子关系，Agent 不创建或自动重建会话。
- `Agent` 的公开表面只包含 `run()`、`steer()`、`abort()`、`resume()`、`wait_for_idle()`、`subscribe()` 和 `close()`；Session CRUD、工作区选择、审批查询和扩展诊断由入口层持有。
- Agent 必须持有当前 Run 的执行任务；WebUI、CLI 和子 Agent 通过 `subscribe()` 观察事件，不得通过消费 Agent async generator 驱动运行。
- 运行中输入只使用一条内存 steering FIFO，并在完整 Turn 后消费；不得提前加入 Follow-up 队列、持久化 Inbox、全局调度器或可脱离 Agent 生命周期的后台 Run 系统。
- `AgentEvent` 必须是 Agent/Turn/Message/Tool/Approval 生命周期的判别联合，不得恢复通用 `type + data` 容器；Provider 增量只能出现在 `message_update.assistant_event`。
- 事件可用内部基类共享运行坐标，但公开 `AgentEvent` 仍须保持具体事件的判别联合。
- 公开事件使用 `session_id + run_id + turn_id`运行坐标，`sequence` 必须在整个 Run 内单调递增。
- `agent_end` 是正常、失败和取消 Run 的唯一终态事件；可恢复运行错误通过该事件传递，不在其后再抛出破坏 SSE。
- 完整消息保存与事件通知分离；订阅者异常或异步等待超时不得破坏消息保存及唯一终态，通知保留有界背压。子 Agent 委派必须读取终态判定成功，并在父任务取消时关闭子 Agent。
- 累计 token 用量仅用于审计；上下文窗口与压缩阈值归 ContextManager/Compaction 管理，不引入累计消耗终止预算或固定重复工具调用次数限制。Turn 数与运行时长限制保留。
- `camel_harness.agent.runtime` 是唯一完整组合模块；Agent 不得依赖 Backend 或 CLI。
- HTTP 路由只做请求校验、状态码和输出投影。
- Backend 的会话模型改绑、级联删除及模型删除必须与 Run 启动共用互斥边界；组合工厂只绑定已存在的会话，不隐式创建。

## LLM、工具与会话

- 默认 Provider 为 Anthropic，同时支持 OpenAI；两者必须使用官方 Python SDK。
- CLI、Backend 与真实评测统一通过 `AgentSettings.load()` 读取 `~/.camel/config.json`；不读取环境变量或项目 `.env`，配置错误不得回显密钥。
- 配置缺失时自动创建默认 JSON，新建目录和文件分别使用 `0700`、`0600` 权限；并发启动不得覆盖已有配置或读取未完成的文件。
- 压缩配置统一放在 `compaction` 对象，不与普通回答输出预算耦合；按 token 估算保留近期完整消息区间，截断摘要最多重试一次且不得落盘，所有已知尝试用量计入 Run，取消不重试。运行时不兼容旧 YAML 或旧平铺压缩字段。
- WebUI 全局配置编辑由 Backend 校验并以 `0600` 权限原子保存；已存密钥不得回传，省略密钥字段须保留旧值，配置文件修改在重启后生效。
- 厂商对象只能存在于 `camel_harness.llm`，对外统一为 `LlmStreamEvent`。
- 规范 assistant 消息组装与流完整性校验归 LLM；Agent 负责实时事件坐标、Run 用量、消息保存点和结束后的控制流，不在组装器中引入 Session 或 Run。
- Adapter 用 `StreamFailed(error, retryable)` 统一表达请求失败；Client 只重试首事件前的瞬时错误，SDK 不叠加重试。LLM/MCP 连接由 runtime 拥有，子 Agent 不关闭借用资源，构造失败必须清理。
- `LlmStreamEvent` 使用具体 dataclass 的判别联合；文本、工具调用、用量和结束原因使用各自字段，不恢复通用 `type + data` 容器或泄漏厂商索引。
- 规范消息必须使用 `Message = UserMessage | AssistantMessage | ToolResultMessage`；系统提示通过 `LlmRequest.system_prompt` 单独传递，不得恢复通用 `role/content` 消息容器。
- 完整 AssistantMessage 保存 Provider 协议标识、请求模型、不同于请求模型的实际响应模型及厂商无关 LlmUsage；未知用量不得伪造为零。规范 input_tokens 含缓存、output_tokens 含推理，明细不重复计入总量；这些审计字段不得回传模型。
- ToolResultMessage.details 保存工具结果的结构化附加信息；大结果使用 details.content_ref 记录文件路径、媒体类型、字节数及原始/预览字符数，不解析展示文本恢复引用，也不因引用开放工作区外的文件权限。
- `AssistantMessage.stop_reason` 必须使用厂商无关的 `LlmStopReason`；它与 Agent Run 级 `StopReason` 是两个独立概念。
- `length` 结束的工具调用禁止执行，必须写入错误工具结果以保持消息链完整。
- 工具截断每个 Run 最多允许一次模型恢复 Turn，不重试历史调用或自动扩大输出额度；纯文本或再次截断结束 Run，取消、权限及 Turn/时长约束继续生效。
- 文件写入与编辑共享规范路径的进程内串行边界；已开始的线程修改必须结束后才释放锁并传播取消，原子替换失败清理临时文件，不承诺取消回滚。
- 模型增量实时转发；只有流正常结束后才能保存完整 assistant 消息，禁止保存 chunk。
- 中断必须向 LLM、审批和工具协程传播；不得保存未完成 assistant，已落盘但缺少结果的工具调用必须补充错误 `ToolResultMessage`，恢复时不得自动重试历史工具。
- `Tool` ABC 及 `ToolContext`、`ToolCall`、`ToolResult` 位于 `camel_harness.agent.tools.types`；给模型使用的 `ToolDefinition` 位于 `camel_harness.llm`，只包含最终工具名、描述和输入 Schema，不感知 namespace。
- 新工具通过 `ToolProvider` 注册，不得修改 Agent 主流程；工具执行必须经过参数校验、权限、审批、超时和结果归一化。
- ToolExecutor 缺省权限必须保守；结果归档失败不能伪装成工具未执行。Shell 只负责输出采集和进程组清理，完整输出与预览归统一结果层管理，不提前丢弃大输出。
- Skill 只保留发现和三层按需加载：系统上下文只列名称与描述，`load_skill` 读取正文或支持文件；不提供 Skill 写入、演化、回滚和评估链路。
- Skill 来源为内置、项目级（工作区 `.camel/skills`）和用户级（`~/.camel/skills`），同名优先级依次递减。
- 顶层会话默认存储在 `~/.camel/sessions/<session-id>/session.jsonl`，子会话递归存储在父会话目录的 `subagents/<session-id>/session.jsonl`；每份日志按类型追加会话头、完整消息、压缩和元数据更新。
- 只有完整规范消息的 `message_end` 可触发消息持久化；Provider 增量、未完成 assistant 和临时审批事件不得写入会话日志。
- 原始消息、模型上下文和元数据由同一日志分别投影；不在运行时兼容旧格式。
- 持久化使用 `SessionEvent` 判别联合，仅包含会话头、完整消息、压缩与元数据更新；不得持久化 AgentEvent 或新增第二份上下文日志。
- 会话头之外的条目持久化 `id/parentId/timestamp`，构成单文件历史树；按当前叶节点恢复上下文与原始分支消息，完整树由 Session.entries 提供，不混入兄弟分支。父子 Agent 目录关系与日志内历史分支是独立概念。
- 新建会话目录和日志使用私有权限；损坏的单份日志不得阻断其他会话的列表读取。
- 已开始的会话提交与叶游标更新必须完成后再传播取消，不得在线程仍写入时释放会话锁或提前进入工具链修复。
- `ContextManager` 与 `CompactionEngine` 位于 `camel_harness.agent.context`，负责动态系统上下文和历史压缩；可依赖 ProjectRules、Skills、Memory 和只读 `ContextSnapshot`，但不得持有 `SessionManager`、执行会话 CRUD 或读写 JSONL。
- ContextManager 统一组装基础与动态提示、选择检索文字并生成压缩计划；Agent 只协调快照读取与计划提交。Context/Compaction 仅接收所需模型标识，不依赖完整 AgentOptions。
- Session 包内部负责树重放；ContextNode 只保留 entry_id 与消息，不使用隐式 seq。压缩通过 firstKeptEntryId 定位保留边界，提交时校验计划快照的 revision 与可见节点，在文件锁内完成父节点选择、校验和追加，保存成功后返回新快照。
- Session.branch 只调整当前绑定的内存叶游标；后续追加形成分支，重启默认恢复最后写入条目。入口只能在 Agent 空闲时选择分支，Agent 不管理分支或会话字典。
- Session 独立负责单个会话的完整消息与压缩投影，不依赖 AgentEvent 或 Agent；Agent 在 `message_end` 内部保存点先追加规范 Message、成功后再通知外部订阅者，并协调 Session 与 ContextManager，不处理 JSONL 格式。
- 规范消息属于 LLM，JSONL 消息编解码属于 Session，LLM 不感知持久化格式。
- 文件与 Shell 工具默认限制在工作区内；不得提交密钥或令牌。

## 前端、测试与文档

- WebUI 依赖方向为 `app -> features -> shared`，feature 之间禁止直接导入。
- 不得通过删除、跳过或弱化测试使构建通过。
- 提交前运行 Ruff、Mypy、Pytest、Import Linter、WebUI build 和 `git diff --check`。
- 架构变化同步更新 `docs/项目架构.md`，计划变化同步更新 `docs/路书.md`。
- `docs/开发日志.md` 按自然日维护，同一天只有一个二级日期标题；每次开发记录主要变更和验证结果。

## git提交规范

- 使用中文提交描述，格式为 `<type>(<scope>): <subject>`，并按需填写正文和尾注。
