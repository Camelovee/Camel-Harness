import pytest

from camel_harness.agent.config import AgentSettings
from camel_harness.agent.runtime import AgentExtensions, build_agent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ApprovalHandler, ToolProvider
from camel_harness.llm import LlmModel

_MODEL = LlmModel("fake", "fake-model")


class DenyApproval(ApprovalHandler):
    async def approve(self, request, reason, on_pending=None):
        return False


async def test_factory_does_not_create_missing_session_or_initialize_sdk(tmp_path):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    with pytest.raises(KeyError, match="会话不存在"):
        await build_agent(AgentSettings(), "missing", tmp_path, DenyApproval(), sessions)
    assert await sessions.get("missing") is None


async def test_factory_rejects_workspace_different_from_session(tmp_path):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("exists", tmp_path, "已存在会话")
    with pytest.raises(ValueError, match="工作区"):
        await build_agent(AgentSettings(), "exists", tmp_path / "other", DenyApproval(), sessions)


@pytest.mark.parametrize("fail", [False, True])
async def test_factory_owns_extension_cleanup_on_success_and_failure(tmp_path, monkeypatch, fail):
    from camel_harness.llm import LlmAdapter, LlmClient

    closed: list[str] = []

    class Adapter(LlmAdapter):
        @property
        def provider_id(self):
            return "fake"

        async def stream(self, request):
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
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("resources", tmp_path, "资源测试")
    extensions = AgentExtensions(
        tool_providers=(ExtensionProvider(),),
        on_close=(close_extension,),
    )
    if fail:
        with pytest.raises(RuntimeError, match="初始化失败"):
            await build_agent(
                AgentSettings(), "resources", tmp_path, DenyApproval(), sessions, extensions
            )
    else:
        agent = await build_agent(
            AgentSettings(), "resources", tmp_path, DenyApproval(), sessions, extensions
        )
        await agent.close()
        await agent.close()
    assert closed == ["extension"]


@pytest.mark.parametrize("enabled", [False, True])
async def test_factory_wires_injected_memory_and_disabled_mode(tmp_path, monkeypatch, enabled):
    from pathlib import Path

    from camel_harness.agent.config import MemorySettings
    from camel_harness.agent.resources import MemoryEntry, MemoryStore
    from camel_harness.agent.tools.memory_tools import MemoryToolProvider
    from camel_harness.llm import LlmAdapter, LlmClient, LlmStopReason, MessageCompleted, TextDelta

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    memories = MemoryStore(tmp_path)
    await memories.save(
        MemoryEntry("language", "中文注释", "只有公开 API 需要 docstring"),
        expected_revision="0",
        session_id="source",
        tool_call_id="seed",
    )
    requests = []

    class Adapter(LlmAdapter):
        @property
        def provider_id(self):
            return "fake"

        async def stream(self, request):
            requests.append(request)
            yield TextDelta("完成")
            yield MessageCompleted(LlmStopReason.STOP)

    monkeypatch.setattr(
        "camel_harness.agent.runtime._build_llm_client", lambda: LlmClient(Adapter())
    )
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("memory", tmp_path, "记忆组合")
    extensions = AgentExtensions(
        tool_providers=(MemoryToolProvider(memories),) if enabled else (),
        memories=memories if enabled else None,
    )
    agent = await build_agent(
        AgentSettings(memory=MemorySettings(enabled=enabled)),
        "memory",
        tmp_path,
        DenyApproval(),
        sessions,
        extensions,
    )
    try:
        await agent.run("中文注释", model=_MODEL)
    finally:
        await agent.close()
    assert len(requests) == 1
    names = {tool.name for tool in requests[0].tools}
    memory_names = {"search_memory", "read_memory", "save_memory", "delete_memory"}
    assert names & memory_names == (memory_names if enabled else set())
    assert ("只有公开 API 需要 docstring" in requests[0].system_prompt) is enabled
    assert [entry.name for entry in await memories.list()] == ["language"]
    assert not (home / ".camel").exists()
