from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from camel_harness.agent.session import Session
from camel_harness.agent.session_committer import SessionCommitter
from camel_harness.agent.state import AgentRun, StopReason
from camel_harness.llm import UserMessage


async def test_complete_message_saves_before_message_end() -> None:
    events = []

    async def emit(event) -> None:
        events.append(event)

    session = AsyncMock(spec=Session)
    agent_run = AgentRun("session", "run")
    agent_run.start()
    committer = SessionCommitter(session, agent_run, emit)

    message = UserMessage("继续")
    await committer.emit_complete_message("turn", message)

    session.append_message.assert_awaited_once_with(message)
    assert [event.type for event in events] == ["message_start", "message_end"]
    assert events[0].role == "user"
    assert events[1].message == message


async def test_save_failure_marks_session_failed_without_message_end() -> None:
    events = []

    async def emit(event) -> None:
        events.append(event)

    session = AsyncMock(spec=Session)
    session.append_message.side_effect = OSError("落盘失败")
    agent_run = AgentRun("session", "run")
    agent_run.start()
    committer = SessionCommitter(session, agent_run, emit)

    with pytest.raises(OSError, match="落盘失败"):
        await committer.finish_message("turn", "message", UserMessage("继续"))

    assert agent_run.stop_reason is StopReason.SESSION_FAILED
    assert [event.type for event in events] == []
