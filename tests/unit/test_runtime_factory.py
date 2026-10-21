from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from camel_harness.agent.config import AgentSettings
from camel_harness.agent.context.system_prompt import DEFAULT_SYSTEM_PROMPT
from camel_harness.agent.runtime import AgentExtensions, build_agent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ApprovalHandler, ToolProvider
from camel_harness.llm import LlmModel

_MODEL = LlmModel("fake", "fake-model")


class DenyApproval(ApprovalHandler):
    async def approve(self, permission_request, reason, on_pending=None):
        return False


async def test_factory_does_not_create_missing_session_or_initialize_sdk(tmp_path):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    with pytest.raises(KeyError, match="会话不存在"):
        await build_agent(
            agent_settings=AgentSettings(),
            session_id="missing",
            workspace=tmp_path,
            approval_handler=DenyApproval(),
            session_manager=session_manager,
        )
    assert await session_manager.get("missing") is None


async def test_factory_rejects_workspace_different_from_session(tmp_path):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("exists", tmp_path, "已存在会话")
    with pytest.raises(ValueError, match="工作区"):
        await build_agent(
            agent_settings=AgentSettings(),
            session_id="exists",
            workspace=tmp_path / "other",
            approval_handler=DenyApproval(),
            session_manager=session_manager,
        )


@pytest.mark.parametrize("fail", [False, True])
async def test_factory_owns_extension_cleanup_on_success_and_failure(tmp_path, monkeypatch, fail):
    from camel_harness.llm import LlmAdapter, LlmClient

    closed: list[str] = []

    class Adapter(LlmAdapter):
        @property
        def provider_id(self):
            return "fake"

        async def stream(self, llm_request):
            if False:
                yield

    class ExtensionProvider(ToolProvider):
        async def discover(self):
            if fail:
                raise RuntimeError("初始化失败")
            return ()

    async def close_extension():
        closed.append("extension")

    monkeypatch.setattr(
        "camel_harness.agent.runtime._build_llm_client", lambda: LlmClient(Adapter())
    )
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("resources", tmp_path, "资源测试")
    agent_extensions = AgentExtensions(
        tool_providers=(ExtensionProvider(),),
        on_close=(close_extension,),
    )
    if fail:
        with pytest.raises(RuntimeError, match="初始化失败"):
            await build_agent(
                agent_settings=AgentSettings(),
                session_id="resources",
                workspace=tmp_path,
                approval_handler=DenyApproval(),
                session_manager=session_manager,
                agent_extensions=agent_extensions,
            )
    else:
        agent = await build_agent(
            agent_settings=AgentSettings(),
            session_id="resources",
            workspace=tmp_path,
            approval_handler=DenyApproval(),
            session_manager=session_manager,
            agent_extensions=agent_extensions,
        )
        await agent.close()
        await agent.close()
    assert closed == ["extension"]


@pytest.mark.parametrize("enabled", [False, True])
async def test_factory_wires_injected_memory_and_disabled_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    from camel_harness.agent.config import MemorySettings
    from camel_harness.agent.resources import MemoryEntry, MemoryStore
    from camel_harness.agent.tools.memory_tools import MemoryToolProvider
    from camel_harness.llm import (
        LlmAdapter,
        LlmClient,
        LlmRequest,
        LlmStopReason,
        LlmStreamEvent,
        MessageCompleted,
        TextDelta,
    )

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    memory_store = MemoryStore(tmp_path)
    await memory_store.save(
        MemoryEntry("language", "中文注释", "只有公开 API 需要 docstring"),
        expected_revision="0",
        session_id="source",
        tool_call_id="seed",
    )
    requests: list[LlmRequest] = []

    class Adapter(LlmAdapter):
        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            requests.append(llm_request)
            yield TextDelta("完成")
            yield MessageCompleted(LlmStopReason.STOP)

    monkeypatch.setattr(
        "camel_harness.agent.runtime._build_llm_client", lambda: LlmClient(Adapter())
    )
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("memory", tmp_path, "记忆组合")
    agent_extensions = AgentExtensions(
        tool_providers=(MemoryToolProvider(memory_store),) if enabled else (),
        memories=memory_store if enabled else None,
    )
    agent = await build_agent(
        agent_settings=AgentSettings(memory=MemorySettings(enabled=enabled)),
        session_id="memory",
        workspace=tmp_path,
        approval_handler=DenyApproval(),
        session_manager=session_manager,
        agent_extensions=agent_extensions,
    )
    try:
        await agent.run("中文注释", model=_MODEL)
        await agent.run("继续检查中文注释", model=_MODEL)
    finally:
        await agent.close()
    assert len(requests) == 2
    for llm_request in requests:
        assert llm_request.system_prompt is not None
        assert llm_request.system_prompt.startswith(DEFAULT_SYSTEM_PROMPT)
        assert llm_request.system_prompt.count(DEFAULT_SYSTEM_PROMPT) == 1
        if not enabled:
            assert llm_request.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert requests[0].system_prompt == requests[1].system_prompt
    names = {tool.name for tool in requests[0].tools}
    memory_names = {"search_memory", "read_memory", "save_memory", "delete_memory"}
    assert names & memory_names == (memory_names if enabled else set())
    assert ("只有公开 API 需要 docstring" in requests[0].system_prompt) is enabled
    assert [entry.name for entry in await memory_store.list()] == ["language"]
    assert not (home / ".camel").exists()
