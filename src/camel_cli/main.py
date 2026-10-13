from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from camel_cli.approval import ConsoleApproval
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.resources import MemoryStore, Skills
from camel_harness.agent.runtime import AgentExtensions, build_agent, build_session_manager
from camel_harness.agent.subagent import SubagentToolProvider
from camel_harness.agent.tools import ToolProvider
from camel_harness.agent.tools.mcp import McpManager
from camel_harness.agent.tools.memory_tools import MemoryToolProvider
from camel_harness.agent.tools.skill_loader import SkillToolProvider

from .terminal import run_once, run_repl

logger = logging.getLogger(__name__)


async def _local_extensions(settings: AgentSettings, workspace: Path) -> AgentExtensions:
    """CLI 注入的本地可选能力：Skill、Memory、MCP 与子 Agent 委派。"""

    skills = Skills(workspace)
    memories = MemoryStore(workspace) if settings.memory.enabled else None
    mcp = McpManager(workspace, workspace / settings.mcp_config)
    await mcp.start()
    for error in mcp.errors:
        logger.warning("MCP 工具加载失败：%s", error)
    providers: list[ToolProvider] = [SkillToolProvider(skills), mcp]
    if memories is not None:
        providers.append(MemoryToolProvider(memories))
    return AgentExtensions(
        tool_providers=tuple(providers),
        skills=skills,
        memories=memories,
        subagent_provider=SubagentToolProvider,
        on_close=(mcp.close,),
    )


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
    latest_session: Annotated[
        bool,
        typer.Option(
            "--latest-session", help="选择当前工作区最近的会话；续跑请在 REPL 中使用 /resume"
        ),
    ] = False,
) -> None:
    """运行一次流式 Harness Agent。"""

    settings = AgentSettings.load()
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
        resolved_session_id = session_id
        if resolved_session_id is not None and latest_session:
            raise typer.BadParameter("--session 与 --latest-session 不能同时使用")
        if latest_session:
            workspace_sessions = tuple(
                session
                for session in await sessions.list()
                if session.workspace == str(resolved_workspace)
                and session.parent_session_id is None
            )
            if not workspace_sessions:
                raise typer.BadParameter("当前工作区没有可选择的历史会话")
            resolved_session_id = workspace_sessions[0].session_id
        resolved_session_id = resolved_session_id or uuid4().hex
        metadata = await sessions.get(resolved_session_id)
        agent_workspace = Path(metadata.workspace) if metadata is not None else resolved_workspace
        await sessions.ensure(resolved_session_id, agent_workspace, "新会话")
        agent = await build_agent(
            settings,
            resolved_session_id,
            agent_workspace,
            ConsoleApproval(),
            sessions=sessions,
            extensions=await _local_extensions(settings, agent_workspace),
        )
        try:
            if prompt is None:
                await run_repl(
                    agent,
                    permission_mode,
                )
            else:
                await run_once(
                    agent,
                    prompt,
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
