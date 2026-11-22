"""在目标平台打包桌面后端，不读取或携带用户配置和会话。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """生成包含解释器和内置资源的目录模式可执行程序。"""

    repository_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "camel-backend",
        "--paths",
        "src",
        "--distpath",
        "desktop/build/backend",
        "--workpath",
        "desktop/build/pyinstaller",
        "--specpath",
        "desktop/build/pyinstaller",
        "--collect-data",
        "camel_harness",
    ]
    # 仅收集 SDK 协议与客户端，避免把可选语音依赖和 MCP CLI 一并导入。
    for package in (
        "anthropic.resources",
        "anthropic.types",
        "openai.resources",
        "openai.types",
        "mcp.client",
        "mcp.shared",
    ):
        command.extend(["--collect-submodules", package])
    for module in (
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ):
        command.extend(["--hidden-import", module])
    # Windows 使用控制台模式保留管道，Electron 的 windowsHide 隐藏控制台窗口。
    command.append("desktop/backend_entry.py")
    subprocess.run(command, cwd=repository_root, check=True)


if __name__ == "__main__":
    main()
