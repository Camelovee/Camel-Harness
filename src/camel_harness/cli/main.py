from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from camel_harness.cli.approval import ConsoleApproval
from camel_harness.harness.config import HarnessSettings
from camel_harness.harness.runtime import build_harness
from camel_harness.types.identifiers import SessionId

from .terminal import run_once, run_repl


def main(
    prompt: Annotated[
        str | None, typer.Argument(help="发送给 Agent 的任务；省略则进入 REPL")
    ] = None,
    session_id: Annotated[str | None, typer.Option("--session", help="复用已有会话 ID")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace", help="Agent 工作区")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="anthropic 或 openai")] = None,
    model: Annotated[str | None, typer.Option("--model", help="模型名称")] = None,
    permission_mode: Annotated[
        str,
        typer.Option(
            "--permission-mode",
            help="权限模式：default、accept_edits、plan 或 yolo",
        ),
    ] = "default",
    resume: Annotated[bool, typer.Option("--resume", help="恢复当前工作区最近会话")] = False,
) -> None:
    """运行一次流式 Harness Agent Turn。"""

    settings = HarnessSettings()
    overrides: dict[str, str] = {}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model
    if overrides:
        settings = HarnessSettings.model_validate({**settings.model_dump(), **overrides})
    resolved_workspace = (workspace or Path.cwd()).resolve()

    async def execute() -> None:
        harness = await build_harness(settings, resolved_workspace, ConsoleApproval())
        try:
            for error in harness.mcp.errors:
                typer.echo(f"[MCP 加载失败] {error}", err=True)
            for error in harness.plugin_errors:
                typer.echo(f"[插件加载失败] {error}", err=True)
            resolved_session_id = session_id
            if resolved_session_id is None and resume:
                workspace_sessions = tuple(
                    session
                    for session in await harness.sessions.list()
                    if session.workspace == str(resolved_workspace)
                )
                resolved_session_id = (
                    workspace_sessions[0].session_id if workspace_sessions else None
                )
            resolved_session_id = resolved_session_id or SessionId.new().value
            if prompt is None:
                await run_repl(
                    harness,
                    resolved_session_id,
                    resolved_workspace,
                    permission_mode,
                )
            else:
                await run_once(
                    harness,
                    prompt,
                    resolved_session_id,
                    resolved_workspace,
                    permission_mode,
                )
        finally:
            await harness.close()

    asyncio.run(execute())


def app() -> None:
    """控制台脚本入口。"""

    typer.run(main)


if __name__ == "__main__":
    app()
