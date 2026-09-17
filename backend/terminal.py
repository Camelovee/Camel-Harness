from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from backend.agent import AgentStreamEventType
from backend.harness import Harness
from backend.types import PermissionMode


async def run_once(
    harness: Harness,
    prompt: str,
    session_id: str,
    workspace: Path,
    permission_mode: str = "default",
) -> None:
    """消费规范流式事件并投影为终端输出。"""

    async for event in harness.run_turn(
        session_id,
        prompt,
        workspace,
        permission_mode=PermissionMode(permission_mode),
    ):
        if event.type is AgentStreamEventType.TEXT_DELTA:
            typer.echo(str(event.data.get("text", "")), nl=False)
        elif event.type is AgentStreamEventType.TOOL_CALL_STARTED:
            typer.echo(f"\n[调用工具] {event.data.get('name', '')}")
        elif event.type is AgentStreamEventType.TOOL_RESULT:
            status = "失败" if event.data.get("is_error") else "完成"
            typer.echo(f"[工具{status}] {event.data.get('name', '')}")
    typer.echo()


async def run_repl(
    harness: Harness,
    session_id: str,
    workspace: Path,
    permission_mode: str = "default",
) -> None:
    """运行可切换权限模式并复用同一 Session 的交互循环。"""

    mode = permission_mode
    typer.echo(f"Camel Harness REPL｜session={session_id}｜mode={mode}")
    typer.echo("命令：/plan /accept-edits /default /yolo /session /exit")
    while True:
        try:
            prompt = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n再见。")
            return
        prompt = prompt.strip()
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            typer.echo("再见。")
            return
        if prompt == "/session":
            typer.echo(session_id)
            continue
        modes = {
            "/plan": "plan",
            "/accept-edits": "accept_edits",
            "/default": "default",
            "/yolo": "yolo",
        }
        if prompt in modes:
            mode = modes[prompt]
            typer.echo(f"权限模式已切换为 {mode}")
            continue
        await run_once(harness, prompt, session_id, workspace, mode)
