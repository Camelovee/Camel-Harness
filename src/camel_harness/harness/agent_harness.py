from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from camel_harness import Agent, AgentOptions, AgentRun, AgentStreamEvent
from camel_harness.harness.compaction import ContextManager
from camel_harness.harness.session import SessionManager, SessionMetadata
from camel_harness.tools import ApprovalCenter
from camel_harness.types import Message, MessageRole, PermissionMode

from .tools import McpManager


@dataclass(slots=True)
class Harness:
    """面向 CLI 和 Web 的运行时门面，协调会话、资源、压缩和 Agent。"""

    agent: Agent
    sessions: SessionManager
    context: ContextManager
    mcp: McpManager
    select_workspace: Callable[[], Awaitable[Path | None]]
    approvals: ApprovalCenter | None = None
    plugin_errors: tuple[str, ...] = ()

    async def run_turn(
        self,
        session_id: str,
        prompt: str,
        workspace: Path,
        *,
        system_prompt: str = "你是一个可靠的 Harness Agent。",
        max_steps: int | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        thinking: bool | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """准备持久化会话和动态上下文，然后流式运行一个 Turn。"""

        options = replace(
            self.agent.options,
            max_steps=max_steps or self.agent.options.max_steps,
            thinking=self.agent.options.thinking if thinking is None else thinking,
        )
        await self.sessions.ensure(session_id, workspace, _default_session_name(prompt))
        await self.sessions.append_message(session_id, Message.text(MessageRole.USER, prompt))
        dynamic_context = await self.context.build(prompt, workspace)
        if dynamic_context:
            system_prompt = f"{system_prompt}\n\n{dynamic_context}"

        async def load_context(run: AgentRun, active_options: AgentOptions) -> tuple[Message, ...]:
            log = await self.sessions.context_log(session_id)
            compacted = await self.context.compact_if_needed(log, run.turn_id, active_options)
            if compacted is not None:
                await self.sessions.replace_context(
                    session_id,
                    compacted.start_seq,
                    compacted.end_seq,
                    compacted.summary_message,
                )
                run.record_tokens(compacted.input_tokens, compacted.output_tokens)
            return await self.sessions.context_messages(session_id)

        async def on_message(message: Message) -> None:
            await self.sessions.append_message(session_id, message)

        async for event in self.agent.run(
            session_id=session_id,
            workspace=workspace,
            system_prompt=system_prompt,
            load_context=load_context,
            on_message=on_message,
            permission_mode=permission_mode,
            options=options,
        ):
            yield event

    async def list_workspaces(
        self,
    ) -> tuple[tuple[str, tuple[SessionMetadata, ...]], ...]:
        """按最近活动时间返回工作区及其会话。"""

        grouped: dict[str, list[SessionMetadata]] = {}
        for session in await self.sessions.list():
            grouped.setdefault(session.workspace, []).append(session)
        items = tuple((workspace, tuple(sessions)) for workspace, sessions in grouped.items())
        return tuple(
            sorted(
                items,
                key=lambda item: max(session.updated_at for session in item[1]),
                reverse=True,
            )
        )

    async def close(self) -> None:
        """关闭 Harness 持有的外部连接。"""

        await self.mcp.close()


def _default_session_name(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:60] or "新会话"
