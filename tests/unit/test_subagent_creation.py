from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.context.system_prompt import SUBAGENT_SYSTEM_PROMPTS
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.subagent import (
    LocalSubagentRunner,
    SubagentRequest,
    SubagentType,
    build_subagent,
)
from camel_harness.agent.tools import PermissionMode, PermissionService, ToolRegistry
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    TextDelta,
)

_MODEL = LlmModel("fake", "model")


class RecordingAdapter(LlmAdapter):
    """记录真正送往模型的配置，验证创建阶段不执行任务。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(llm_request)
        yield TextDelta("完成")
        yield MessageCompleted(LlmStopReason.STOP)


@pytest.mark.parametrize("agent_type", list(SubagentType))
async def test_creation_is_lazy_and_keeps_role_and_registry_isolation(
    tmp_path: Path, agent_type: SubagentType
) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    parent_tool_names = {definition.name for definition in tool_registry.llm_definitions()}
    adapter = RecordingAdapter()
    llm_client = LlmClient(adapter)
    context_manager = ContextManager(
        system_prompt="父提示", compaction_engine=CompactionEngine(llm_client)
    )
    subagent_request = SubagentRequest(
        task="子任务",
        agent_type=agent_type,
        workspace=tmp_path,
        permission_mode=PermissionMode.DEFAULT,
        parent_session_id="parent",
        parent_tool_call_id="delegate",
        model=_MODEL,
    )

    async def create_child() -> Agent:
        return await build_subagent(
            subagent_request,
            llm_client=llm_client,
            session_manager=session_manager,
            tool_registry=tool_registry,
            permission_service=PermissionService(),
            context_manager=context_manager,
            agent_options=AgentOptions(),
        )

    agent = await create_child()
    assert not adapter.requests
    assert not agent.is_running
    assert await session_manager.bind(agent.session_id).messages() == ()
    metadata = await session_manager.get(agent.session_id)
    assert metadata is not None
    assert metadata.parent_session_id == "parent"
    assert metadata.parent_tool_call_id == "delegate"
    assert metadata.task_outcome is None
    try:
        await agent.run(subagent_request.task, model=_MODEL)
    finally:
        await agent.close()

    # 关闭已创建的子 Agent 后，共享依赖仍能用于另一个独立子会话。
    second_agent = await create_child()
    assert second_agent.session_id != agent.session_id
    try:
        await second_agent.run(subagent_request.task, model=_MODEL)
    finally:
        await second_agent.close()
    expected_tool_names = parent_tool_names if agent_type is SubagentType.GENERAL else {"read_file"}
    assert len(adapter.requests) == 2
    for llm_request in adapter.requests:
        assert {definition.name for definition in llm_request.tools} == expected_tool_names
        assert llm_request.system_prompt is not None
        assert llm_request.system_prompt.startswith(SUBAGENT_SYSTEM_PROMPTS[agent_type.value])
    assert {definition.name for definition in tool_registry.llm_definitions()} == parent_tool_names
    assert await session_manager.bind("parent").messages() == ()


async def test_runner_propagates_creation_failure_without_fabricating_terminal(
    tmp_path: Path,
) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    subagent_request = SubagentRequest(
        "任务", SubagentType.GENERAL, tmp_path, PermissionMode.DEFAULT, "parent", "delegate", _MODEL
    )
    received_requests: list[SubagentRequest] = []

    async def fail_creation(subagent_request: SubagentRequest) -> Agent:
        received_requests.append(subagent_request)
        raise RuntimeError("创建子任务失败")

    subagent_runner = LocalSubagentRunner(
        build_subagent=fail_creation, session_manager=session_manager
    )
    with pytest.raises(RuntimeError, match="创建子任务失败"):
        await subagent_runner.run(subagent_request)
    assert received_requests == [subagent_request]
    sessions = await session_manager.list()
    assert len(sessions) == 1
    assert sessions[0].task_outcome is None
