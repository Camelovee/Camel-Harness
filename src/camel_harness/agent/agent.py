from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from camel_harness.llm import LlmClient, Message, UserMessage

from .agent_loop import agent_loop
from .compaction import ContextManager
from .events import AgentStreamEvent
from .session import SessionManager
from .state import AgentOptions, AgentRun
from .tools import PermissionMode, ToolExecutor, ToolRegistry

CloseHandler = Callable[[], Awaitable[None]]


class Agent:
    """持有运行依赖，并通过最小 Agent Loop 执行一次 Turn。"""

    def __init__(
        self,
        options: AgentOptions,
        llm: LlmClient,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
        sessions: SessionManager,
        context: ContextManager,
        on_close: CloseHandler | None = None,
    ) -> None:
        self._options = options
        self._llm = llm
        self._tools = tools
        self._tool_executor = tool_executor
        self._sessions = sessions
        self._context = context
        self._on_close = on_close
        self._closed = False

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
        """准备会话和动态上下文，然后流式运行一个 Turn。"""

        options = replace(
            self._options,
            max_steps=max_steps or self._options.max_steps,
            thinking=self._options.thinking if thinking is None else thinking,
        )
        await self._sessions.ensure(session_id, workspace, _default_session_name(prompt))
        await self._sessions.append_message(session_id, UserMessage(prompt))
        dynamic_context = await self._context.build(prompt, workspace)
        if dynamic_context:
            system_prompt = f"{system_prompt}\n\n{dynamic_context}"

        async def load_context(run: AgentRun, active_options: AgentOptions) -> tuple[Message, ...]:
            log = await self._sessions.context_log(session_id)
            compacted = await self._context.compact_if_needed(log, active_options)
            if compacted is not None:
                await self._sessions.replace_context(
                    session_id,
                    compacted.start_seq,
                    compacted.end_seq,
                    compacted.summary_message,
                )
                run.record_tokens(compacted.input_tokens, compacted.output_tokens)
            return await self._sessions.context_messages(session_id)

        async def on_message(message: Message) -> None:
            await self._sessions.append_message(session_id, message)

        async for event in agent_loop(
            llm=self._llm,
            tools=self._tools,
            tool_executor=self._tool_executor,
            session_id=session_id,
            workspace=workspace,
            system_prompt=system_prompt,
            options=options,
            load_context=load_context,
            on_message=on_message,
            permission_mode=permission_mode,
        ):
            yield event

    async def close(self) -> None:
        """幂等关闭 Agent 持有的外部连接。"""

        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            await self._on_close()


def _default_session_name(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:60] or "新会话"
