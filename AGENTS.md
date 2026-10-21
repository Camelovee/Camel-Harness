# Camel Harness 项目约束

本文件对整个仓库生效，只保留代码规范和基本项目约束；实现细节放在代码、测试及架构文档中。

## 代码规范

- 标识符使用英文；注释、docstring、错误说明和项目文档默认使用中文。
- 注释说明设计原因、边界和不变量，不逐行翻译代码。
- 保持实现简洁，避免过度抽象、重复职责和无需求的目录层级。
- 不保留废弃导入路径、空壳模块和无明确需求的兼容代码。

### 代码质量规范（Code quality standards）

- 所有 Python 代码必须包含类型注解，函数和方法必须声明参数类型与返回类型（包括 `-> None`）。
- 使用含义明确、能说明用途的变量名；源码、脚本和测试遵循相同规则。
- 依赖对象按职责命名，例如 `agent_options`、`llm_client`、`tool_registry`、`context_manager`、`session_manager`、`session_storage`、`settings_store`；参数、局部变量和对应实例属性使用一致名称，不以 `options`、`llm`、`tools` 等笼统名称表示具体依赖。
- 保留 LLM、MCP、ID 等通用术语；区分对象、定义集合和索引映射，例如 `tool_registry`、`tool_definitions`、`tools_by_name`。
- 请求、结果和事件在跨组件时标明所属领域，例如 `llm_request`、`permission_request`、`tool_result`；依赖较多的组装调用使用关键字参数。
- 同一作用域存在多个同类实例时，用角色或状态区分，例如 `restarted_session_manager`、`disabled_context_manager`，不得因统一命名混淆独立实例。
- 重命名同步更新调用方、关键字参数、测试替身和反射引用；保持类型名、模块路径及对外协议、配置和持久化字段不变，不做无差别文本替换。
- 遵循当前修改模块已有的代码模式与约定。
- 对复杂函数，在职责可以清晰拆分时提取为更小、职责集中的函数，避免为凑行数机械拆分。
- 函数 docstring 以单行摘要开头；需要补充上下文时另起段落，参数和返回值说明使用 `Args`、`Returns` 段落。描述内容默认使用中文。

```python
def filter_unknown_users(users: list[str], known_users: set[str]) -> list[str]:
    """筛选未知用户标识。

    保留输入顺序和重复项。

    Args:
        users: 待筛选的用户标识列表。
        known_users: 已知或有效用户标识的集合。

    Returns:
        不在 known_users 集合中的用户标识列表。
    """
    return [user for user in users if user not in known_users]
```

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
- 开发、修复功能时在分支中进行，每完成一个任务就在分支中提交。
- main分支的合并和提交远程git仓库需要用户命令，不擅自提交。

## MCP 集成

- RepoAtlas 是可选 MCP 集成（`mcp__repoatlas__*` 工具），用于代码库检索 / Wiki / 问答。公开配置见 `.mcp.example.json`，本机配置保存在不纳入 Git 的 `.mcp.json`；按本机安装位置调整配置路径。
- `McpManager` 强制 MCP server 的 `cwd = workspace`；通过 `--project <RepoAtlas路径>` 指定本机项目目录，必要时使用 `uv` 的绝对路径，不依赖 `cwd` 字段。
