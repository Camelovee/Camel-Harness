"""完整消息的保存与确认事件，保持先落盘后通知的顺序。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from camel_harness.llm import Message

from .events import AgentEvent, MessageEndEvent, MessageKind, MessageStartEvent
from .session import Session
from .state import AgentRun, RunStatus, StopReason

EmitEvent = Callable[[AgentEvent], Awaitable[None]]


class SessionCommitter:
    """为单个 Run 保存完整消息，并在保存成功后发送开始/结束事件。"""

    def __init__(self, session: Session, agent_run: AgentRun, emit: EmitEvent) -> None:
        self._session = session
        self._agent_run = agent_run
        self._emit = emit

    async def emit_message_start(self, turn_id: str, message_id: str, role: MessageKind) -> None:
        """发送消息开始事件；此时消息尚未生成或保存。"""

        await self._emit(
            MessageStartEvent(
                session_id=self._agent_run.session_id,
                run_id=self._agent_run.run_id,
                sequence=self._agent_run.next_sequence(),
                turn_id=turn_id,
                message_id=message_id,
                role=role,
            )
        )

    async def finish_message(self, turn_id: str, message_id: str, message: Message) -> None:
        """先保存完整消息再确认完成；保存失败标记会话故障且不发送确认。"""

        try:
            await self._session.append_message(message)
        except Exception:
            if self._agent_run.status is RunStatus.RUNNING:
                self._agent_run.fail(StopReason.SESSION_FAILED)
            raise
        await self._emit(
            MessageEndEvent(
                session_id=self._agent_run.session_id,
                run_id=self._agent_run.run_id,
                sequence=self._agent_run.next_sequence(),
                turn_id=turn_id,
                message_id=message_id,
                message=message,
            )
        )

    async def emit_complete_message(
        self,
        turn_id: str,
        message: Message,
        *,
        message_id: str | None = None,
    ) -> None:
        """为不再流式的完整消息共享开始、保存和结束路径。"""

        resolved_message_id = message_id or uuid4().hex
        await self.emit_message_start(turn_id, resolved_message_id, message.role)
        await self.finish_message(turn_id, resolved_message_id, message)
