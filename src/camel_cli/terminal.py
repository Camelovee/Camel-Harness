from __future__ import annotations

import asyncio

import typer

from camel_harness.agent import Agent, AgentEvent
from camel_harness.agent.tools import PermissionMode
from camel_harness.llm import LlmModel

from .approval import console_input


async def run_once(
    agent: Agent,
    prompt: str,
    model: LlmModel,
    permission_mode: str = "default",
) -> None:
    """订阅规范事件并投影为终端输出。"""

    unsubscribe = agent.subscribe(_render_event)
    try:
        await agent.run(
            prompt,
            model=model,
            permission_mode=PermissionMode(permission_mode),
        )
    finally:
        unsubscribe()
        typer.echo()


async def resume_once(agent: Agent, model: LlmModel, permission_mode: str = "default") -> None:
    """从当前 Agent 绑定的 Session 尾部继续并投影流式事件。"""

    unsubscribe = agent.subscribe(_render_event)
    try:
        await agent.resume(model=model, permission_mode=PermissionMode(permission_mode))
    finally:
        unsubscribe()
        typer.echo()


def _render_event(event: AgentEvent) -> None:
    """把一条 AgentEvent 投影为终端增量。"""

    if event.type == "message_update" and event.assistant_event.type == "text_delta":
        typer.echo(event.assistant_event.text, nl=False)
    elif event.type == "tool_execution_start":
        typer.echo(f"\n[调用工具] {event.name}")
    elif event.type == "tool_execution_end":
        status = "失败" if event.result.is_error else "完成"
        typer.echo(f"[工具{status}] {event.result.name}")
    elif event.type == "agent_end" and event.status != "completed":
        typer.echo(f"\n[Agent {event.status}] {event.error or event.stop_reason}", err=True)


async def run_repl(
    agent: Agent,
    model: LlmModel,
    permission_mode: str = "default",
) -> None:
    """运行可切换权限模式并复用同一 Session 的交互循环。"""

    mode = permission_mode
    typer.echo(f"Camel Harness REPL｜session={agent.session_id}｜mode={mode}")
    typer.echo("命令：/plan /accept-edits /default /yolo /session /resume /cancel /exit")
    active_task: asyncio.Task[None] | None = None
    while True:
        if active_task is not None and active_task.done():
            try:
                await active_task
            except Exception as exc:
                typer.echo(f"\nAgent 执行失败：{exc}")
            active_task = None
        try:
            prompt = await console_input("\n> ")
        except KeyboardInterrupt:
            if active_task is not None:
                await agent.abort()
                await agent.wait_for_idle()
                active_task = None
                typer.echo("\n当前 Run 已取消。")
                continue
            typer.echo("\n再见。")
            return
        except EOFError:
            if active_task is not None:
                await agent.abort()
                await agent.wait_for_idle()
            typer.echo("\n再见。")
            return
        prompt = prompt.strip()
        if not prompt:
            continue
        if active_task is not None and active_task.done():
            try:
                await active_task
            except Exception as exc:
                typer.echo(f"\nAgent 执行失败：{exc}")
            active_task = None
        if prompt in {"/exit", "/quit"}:
            if active_task is not None:
                await agent.abort()
                await agent.wait_for_idle()
            typer.echo("再见。")
            return
        if prompt == "/session":
            typer.echo(agent.session_id)
            continue
        if prompt == "/cancel":
            if not await agent.abort():
                typer.echo("当前没有活动 Run。")
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
        if active_task is not None:
            try:
                queued = await agent.steer(prompt)
                typer.echo(f"已追加运行中输入：{queued.input_id}")
            except RuntimeError as exc:
                typer.echo(str(exc))
            continue
        if prompt == "/resume":
            active_task = asyncio.create_task(resume_once(agent, model, mode))
        else:
            active_task = asyncio.create_task(run_once(agent, prompt, model, mode))
