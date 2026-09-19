from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from camel_cli.approval import ConsoleApproval
from camel_harness.agent import SessionId
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.runtime import build_agent, build_session_manager

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
    """运行一次流式 Harness Agent。"""

    settings = AgentSettings()
    overrides: dict[str, str] = {}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model
    if overrides:
        settings = AgentSettings.model_validate({**settings.model_dump(), **overrides})
    resolved_workspace = (workspace or Path.cwd()).resolve()

    async def execute() -> None:
        sessions = build_session_manager(settings)
        agent = await build_agent(
            settings,
            resolved_workspace,
            ConsoleApproval(),
            sessions=sessions,
        )
        try:
            resolved_session_id = session_id
            if resolved_session_id is None and resume:
                workspace_sessions = tuple(
                    session
                    for session in await sessions.list()
                    if session.workspace == str(resolved_workspace)
                )
                resolved_session_id = (
                    workspace_sessions[0].session_id if workspace_sessions else None
                )
            resolved_session_id = resolved_session_id or SessionId.new().value
            if prompt is None:
                await run_repl(
                    agent,
                    resolved_session_id,
                    resolved_workspace,
                    permission_mode,
                )
            else:
                await run_once(
                    agent,
                    prompt,
                    resolved_session_id,
                    resolved_workspace,
                    permission_mode,
                )
        finally:
            await agent.close()

    asyncio.run(execute())


def app() -> None:
    """控制台脚本入口。"""

    typer.run(main)


if __name__ == "__main__":
    app()
