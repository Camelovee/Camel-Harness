# RepoAtlas MCP 集成演示

Camel-Harness 通过 MCP 调用 RepoAtlas 的检索 / Wiki 工具。`McpManager` 从工作区读取 `.mcp.json`，以 stdio 启动服务；这是可选集成，未配置不影响内置工具。

## 本机配置

前提：本机已安装 `uv`，并准备好 RepoAtlas 项目及其依赖。将根目录 `.mcp.example.json` 复制为 `.mcp.json`，不要覆盖已有配置。替换以下占位符：

- `<REPOATLAS_PATH>`：本机 RepoAtlas 项目的绝对路径。
- `command`：默认使用 PATH 中的 `uv`；若启动进程找不到它，替换为本机可执行文件的绝对路径。

```json
{
  "mcpServers": {
    "repoatlas": {
      "command": "uv",
      "args": ["run", "--project", "<REPOATLAS_PATH>", "repoatlas-mcp"]
    }
  }
}
```

`McpManager` 强制服务的 `cwd = workspace`，因此通过 `--project` 指定 RepoAtlas，不能依赖配置中的 `cwd`。Windows 路径可使用正斜杠；若使用反斜杠，JSON 中需写为 `\\`。本机 `.mcp.json` 已被 Git 忽略，不应提交路径或凭据。

## 索引与运行

在 RepoAtlas 目录执行以下命令，将 `<CAMEL_HARNESS_PATH>` 替换为 Camel-Harness 的绝对路径：

```bash
uv run repoatlas index "<CAMEL_HARNESS_PATH>"
```

记录本次索引返回的仓库 ID，不复用他人机器的 ID；仓库变更后按 RepoAtlas 的索引流程更新。回到 Camel-Harness 目录，按 README 配置模型，将 `<REPO_ID>` 替换为索引结果后运行：

```bash
uv run camel-harness "用 repoatlas 的 search 工具在 <REPO_ID> 仓库里检索权限审批的实现，再按检索结果选择相应 Wiki 页面，说明授权与审批的边界，并标明证据文件。"
```

使用默认权限模式，按实际提示审批，不要求 YOLO。该步骤调用真实模型，可能产生费用；普通测试不执行它。

## 验收与排查

- 观察 `mcp__repoatlas__search` 等实际工具调用及其结果，确认回答引用当前仓库文件，而不是仅复述提示词。
- Wiki 页面 ID 以当前索引提供的目录或检索结果为准，不硬编码旧模块页。
- 无法启动服务时检查 `uv`、`--project` 路径与 RepoAtlas 依赖；无法命中代码时检查仓库 ID 和索引版本。
- 公开演示只记录必要工具名、相对文件路径和结果摘要；删除机器路径、凭据及私人对话。本页为通用复现说明，不代表新机器或当前版本已完成真实模型验收。
