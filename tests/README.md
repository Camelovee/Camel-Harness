# 回归测试说明

测试优先验证可观察行为：最终文件、完整会话、工具副作用次数、唯一终态和资源释放。取消、超时、持久化失败和审批拒绝虽可能进入同一清理路径，但触发时机与副作用不同，需要分别保留。

## 覆盖边界

| 范围 | 主要测试 | 保留原因 |
| --- | --- | --- |
| 模型协议 | `contract/test_provider_*`、`unit/test_llm_*` | 官方 SDK 的事件映射、用量、工具参数完整性和首事件前重试 |
| Agent 运行 | `acceptance/` | steering、并发启动、取消与恢复、事件保存顺序、超限和截断恢复、子任务资源所有权 |
| 文件与 Shell | `integration/test_file_mutations.py`、`test_shell_output.py`、`test_tool_storage_failure.py` | 原子替换、排队/在途取消、进程树清理及归档失败后不重复副作用 |
| 会话与记忆 | `integration/test_session_*`、`test_jsonl_session_store.py`、`test_memory.py`、`test_migrate_memory.py` | 分支重放、损坏日志、跨进程竞争、记忆字符上限、权限、路径边界和旧数据迁移 |
| Web 入口 | HTTP/SSE、设置与多会话测试，`webui/tests/` | 请求契约、断连取消、审批与并行会话隔离 |
| 模块依赖 | `uv run lint-imports`、`unit/test_webui_architecture.py` | Python 单向依赖和前端 `app → features → shared` 边界 |

## 精简原则

- Python 导入边界统一在 `pyproject.toml` 的 Import Linter 契约中维护，包含 Runtime 的可选 Provider 注入和 Context 对会话存储的隔离。
- 不通过 AST 穷举目录、历史文件名、全部字段或私有方法名。模型/事件契约由序列化与行为测试验证，核心工具集合由 `unit/test_tooling.py` 验证。
- 同一公开行为保留最直接的验证：配置默认/相对路径归入 `unit/test_settings.py`；LLM 路由和支持查询归入 `unit/test_llm_retry.py`；大用量不终止 Run、按 Run 选模型由 acceptance 用例覆盖。
- 正常写入和编辑由文件修改测试覆盖；危险 Shell 拒绝归入 Shell 测试。删除功能时同步移除其专用测试，避免留下失效入口。
- 新增故障回归需说明触发条件和可观察后果；仅输入值不同且覆盖不同边界时使用参数化，不为凑数量重复同一断言。

## 运行

```bash
uv run pytest -q
uv run lint-imports
npm test --prefix webui
```

测试使用临时工作区、假模型、本地 HTTP/SSE 或本地 MCP Server，不调用真实模型 Provider。
