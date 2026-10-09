import asyncio

import pytest

from camel_harness.agent import AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import PermissionMode, PermissionService, ToolRegistry
from camel_harness.agent.tools.subagent import LocalSubagentRunner, SubagentRequest, SubagentType
from camel_harness.llm import LlmAdapter, LlmClient, StreamFailed, TextDelta


class ChildLlm(LlmAdapter):
    """可阻塞或失败的子模型，用于确认委派任务清理边界。"""

    def __init__(self, blocking: bool) -> None:
        self.blocking = blocking
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    @property
    def provider_id(self):
        return "fake"

    async def stream(self, request):
        try:
            yield TextDelta("未完成的回答")
            self.started.set()
            if self.blocking:
                await asyncio.Event().wait()
            else:
                yield StreamFailed("模型失败")
        finally:
            self.closed.set()


async def make_runner(tmp_path, adapter):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    llm = LlmClient(adapter)
    runner = LocalSubagentRunner(
        llm,
        sessions,
        ToolRegistry(),
        PermissionService(),
        ContextManager(CompactionEngine(llm)),
        AgentOptions("fake"),
    )
    request = SubagentRequest(
        "子任务", SubagentType.EXPLORE, tmp_path, PermissionMode.DEFAULT, "parent", "delegate"
    )
    return runner, request, sessions


async def test_child_failed_terminal_is_not_success_or_partial_text(tmp_path):
    adapter = ChildLlm(False)
    runner, request, sessions = await make_runner(tmp_path, adapter)
    result = await runner.run(request)
    assert not result.succeeded
    assert result.content == "模型失败"
    assert [message.role for message in await sessions.bind(result.session_id).messages()] == [
        "user"
    ]
    assert adapter.closed.is_set()


async def test_parent_cancellation_closes_child_owned_task(tmp_path):
    adapter = ChildLlm(True)
    runner, request, sessions = await make_runner(tmp_path, adapter)
    task = asyncio.create_task(runner.run(request))
    await adapter.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert adapter.closed.is_set()
    children = [item for item in await sessions.list() if item.parent_session_id == "parent"]
    assert len(children) == 1
    assert [message.role for message in await sessions.bind(children[0].session_id).messages()] == [
        "user"
    ]
