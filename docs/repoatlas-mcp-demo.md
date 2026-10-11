# RepoAtlas 集成 demo（Camel agent 把 RepoAtlas 当代码库知识工具）

Camel-Harness 的 agent 通过 MCP 调用 RepoAtlas 的检索 / Wiki 工具，查询 Camel-Harness 自身
的代码库。集成点是根目录 `.mcp.json`（McpManager 从 workspace 读取，stdio 拉起 RepoAtlas）。

## 复现

```bash
# 1. 确保 RepoAtlas 已索引 Camel-Harness（在 RepoAtlas 目录）
cd ../RepoAtlas && uv run repoatlas index /Users/camel/agentProject/Camel-Harness

# 2. 在 Camel-Harness 目录让 agent 用 RepoAtlas 工具（yolo 跳过交互审批）
uv run camel-harness --permission-mode yolo \
  "用 repoatlas 的 search 工具在 Camel-Harness-e8766842 仓库里检索'权限审批是怎么实现的'，
   再用 wiki_page 读 module/src.camel_harness.tools 页面，然后简要回答"
```

## 实际运行记录（2026-10-11）

agent 依次调用了两个 MCP 工具，并基于检索结果回答：

- `[调用工具] mcp__repoatlas__search`  → 命中 `src/camel_harness/tools/permissions.py:97`（PermissionService）
- `[调用工具] mcp__repoatlas__wiki_page` → 读取模块页 `module/src.camel_harness.tools`
- 最终回答正确概述了 PermissionService 三段式授权、策略/审批解耦、ApprovalHandler 可替换审批通道，
  并注明"信息来源：RepoAtlas 对仓库的检索结果及 Wiki 页面"。

`.mcp.json` 配置（注意：McpManager 强制 cwd = workspace，故用绝对路径 uv + --project 指定 RepoAtlas）：

```json
{
  "mcpServers": {
    "repoatlas": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--project", "/Users/camel/agentProject/RepoAtlas", "repoatlas-mcp"]
    }
  }
}
```
