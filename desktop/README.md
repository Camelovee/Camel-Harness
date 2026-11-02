# Camel Harness 桌面预览版

Electron 是唯一产品入口，工程内包含 React 渲染界面和 Python/FastAPI 后端集成。当前开发分支为 `desktop-dev`，版本为 1.1.0 预览版；打包目标为 macOS Apple Silicon、macOS Intel 和 Windows x64。

## 下载安装

截至 2026-11-02，1.1.0 桌面安装包尚未发布，已有的 1.0.1 Release 不包含桌面客户端。当前请从 `desktop-dev` [运行源码或构建安装包](#源码开发与打包)。维护者完成平台验证并发布后，可从 [GitHub Releases](https://github.com/Camelovee/Camel-Harness/releases) 选择以下安装包。

| 系统 | 文件 |
| --- | --- |
| Apple Silicon Mac | `Camel-Harness-<版本>-mac-arm64.dmg` |
| Intel Mac | `Camel-Harness-<版本>-mac-x64.dmg` |
| Windows x64 | `Camel-Harness-<版本>-win-x64.exe` |

macOS 打开 DMG，将应用拖到 Applications；Windows 运行安装程序，可选择安装目录。使用者无需另外安装 Python、Node.js 或 uv。

当前未完成正式签名：macOS 只有 ad-hoc 本地签名，没有开发者身份签名和公证；Windows 没有证书签名。系统可能阻止打开，请核验来源和校验和，再按系统提供的单应用授权流程操作；受组织策略管理的电脑可能无法运行预览版。

Release 附带 `SHA256SUMS.txt`。macOS 使用 `shasum -a 256 <安装包>`，Windows PowerShell 使用 `Get-FileHash <安装包> -Algorithm SHA256` 比对摘要。

## 首次使用

1. 在首次启动的模型设置中添加 Anthropic 或 OpenAI 模型和自己的 API Key。
2. 选择本地项目，输入任务。配置和打开目录本身不会调用模型。
3. 在消息中核对工具的目录、命令和参数，决定是否批准。
4. 可在运行中追加指令、停止或按提示恢复。关闭窗口时，活动任务需确认后才停止退出。

配置和会话仍位于 `~/.camel/config.json`、`~/.camel/sessions/`，Windows 对应 `%USERPROFILE%\.camel\`。更新和卸载程序不主动删除这些文件。主题写入配置的 `theme` 字段，支持 `system`、`light`、`dark`。

## 工作台操作

界面采用暖白 / 中性夜色的简洁三栏布局，两侧可独立收起。动效响应系统“减少动态效果”偏好。

| 区域 | 操作与行为 |
| --- | --- |
| 工作区与会话 | 点击当前工作区名称展开或收起，不切换会话、不清空草稿，也不停止任务；切换或新建会话时重新展示对应分组 |
| 分隔线 | 拖动调整两侧宽度，双击复位；方向键每次微调 16 px，Shift 加方向键为 32 px；点击可打开缩小、增大及重置操作 |
| 输入框 | 默认单行紧凑显示，与聊天记录等宽；多行草稿自动增高至 180 px 后内部滚动。Enter 发送，Shift + Enter 换行，中文输入法确认候选词不会直接发送 |
| 执行详情 | 展示真实状态、用量和工具活动；点击文件或子任务查看详情。窄窗口使用可按 Escape 关闭的模态面板 |
| 设置 | 头部和左侧分类固定，右侧内容独立滚动；切换分类回到内容顶部，弹窗不重新居中移动 |

分栏宽度只在本次应用会话内保留；主题写入用户配置并在下次启动继续使用。欢迎页建议只填写草稿，不自动发起任务。

[查看设置界面预览](../docs/images/desktop-settings.png)

快捷键：新对话 `⌘/Ctrl+N`、打开项目 `⌘/Ctrl+O`、切换侧栏 `⌘/Ctrl+B`、设置 `⌘/Ctrl+,`。首版不提供托盘后台任务或自动更新。

应用内置自己的 Python 后端；执行项目命令所需的 Git、语言环境、编译器及可选 MCP 程序仍由用户按需安装。

## 源码开发与打包

需要 Python 3.11+、Node.js 24 和 uv。首次获取桌面分支：

```bash
git clone --branch desktop-dev https://github.com/Camelovee/Camel-Harness.git
cd Camel-Harness
```

后续命令在仓库根目录执行：

```bash
uv sync --locked --extra dev --extra desktop --python 3.11
npm ci --prefix desktop
npm run dev --prefix desktop
```

开发模式先构建主进程、预加载与 React 渲染界面，再自动启动仓库内后端。修改代码后关闭应用并重新执行启动命令。`desktop/renderer/src/` 保持 `app → features → shared` 分层，`desktop/tests/renderer/` 维护界面回归；全部依赖、类型检查和构建使用同一份 npm 清单与锁文件，不再保留独立 Web 工程或浏览器启动方式。

源码与产物分布如下，所有生成目录均不纳入 Git：

| 位置 | 内容 |
| --- | --- |
| `src/` | Electron 主进程、预加载与后端进程管理 |
| `bridge.d.ts` | 主进程、预加载与渲染界面共用的桥接类型，无运行时代码 |
| `renderer/src/` | React 界面，设置状态由 `useSettings` 管理 |
| `assets/`、`scripts/`、`tests/` | 应用静态资源、构建/验收脚本与回归测试 |
| `build/backend/`、`build/pyinstaller/` | 独立后端产物与 PyInstaller 中间文件 |
| `dist/`、`release/` | 编译后的桌面代码与界面、可分发安装包 |

在目标操作系统和 CPU 架构上生成安装包：

```bash
uv run python scripts/build_desktop_backend.py
npm run dist --prefix desktop -- --publish never
uv run python scripts/desktop_checksums.py desktop/release
```

产物在 `desktop/release/`。不能用一个 Python 可执行文件跨平台分发。Windows 后端保留控制台模式以支持管道，由 Electron 隐藏窗口，不能改为 PyInstaller 无控制台模式。

离线验收使用临时用户目录和本机模型协议替身，不读取私人配置，不调用外部模型：

```bash
npm run test:smoke --prefix desktop
# 打包后的 Apple Silicon 应用；Intel 将 mac-arm64 改为 mac
node desktop/scripts/smoke.cjs "desktop/release/mac-arm64/Camel Harness.app/Contents/MacOS/Camel Harness"
# Windows
node desktop/scripts/smoke.cjs "desktop/release/win-unpacked/Camel Harness.exe"
```

## GitHub 发布

本机验证覆盖 Apple Silicon Mac 的独立后端构建、DMG 生成、只读挂载、复制到带中文和空格的临时安装目录，以及移除开发工具路径后的真实应用验收。最新界面回归包含紧凑输入区、多行伸缩与清空恢复、分栏拖动、工作区折叠、消息输入等宽、设置分类及长表单滚动时的头部稳定。

Anthropic/OpenAI 均使用本机协议替身，审批写入、主题、详情缩放、取消退出、确认退出和未完成响应不落盘通过。Windows、Intel Mac 的原生构建及实际安装尚待对应平台验证，不能视为已通过。

Desktop 工作流在三个原生 runner 上检查、打包并验证真实应用。PR 和手动运行只上传 Actions 产物。维护者推送与工程版本一致的 `v<版本>` 标签后，工作流汇总安装包和摘要，创建 **draft + prerelease**，等待维护者检查后发布，不自动改写已发布的 Release。

普通分支推送只触发 CI，不上传安装包或创建 Release。三平台打包需通过 Desktop 工作流的手动入口、匹配路径的 PR 或版本标签触发；工作流定义见 [desktop.yml](../.github/workflows/desktop.yml)。

取得证书后另行接入 macOS Developer ID 签名、公证和 Windows 签名。发布前应补齐三个平台的实际安装检查，不能以其他系统的模拟结果代替。

## 故障排查

- **服务启动失败**：检查配置是否为合法 JSON、重新安装能否补齐资源。故障页只主动重启服务，不重放任务。启动等待上限 30 秒。
- **退出超时**：15 秒后请求强制结束所属进程并提示，重新打开后先核验文件和命令结果，再决定是否恢复。
- **命令找不到**：确认项目工具或 MCP 程序已安装，且桌面进程的 PATH 能找到。macOS 从 Finder 启动可能看不到终端设置的路径，可从配置好 PATH 的终端直接执行应用的 `Contents/MacOS/Camel Harness`；MCP 配置优先填写程序绝对路径。
- **Windows Shell**：工具仍使用 `cmd.exe` 语法，不是 PowerShell。

Electron 渲染进程隔离不等于 Agent 工具的 OS 沙箱。文件和 Shell 操作继续遵守原有工作区与审批边界。
