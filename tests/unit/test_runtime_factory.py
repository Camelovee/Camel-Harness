import pytest

from camel_harness.agent.config import AgentSettings
from camel_harness.agent.runtime import build_agent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ApprovalHandler


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
async def test_factory_owns_llm_and_mcp_cleanup_on_success_and_failure(tmp_path, monkeypatch, fail):
    from camel_harness.llm import LlmAdapter

    closed: list[str] = []

    class Adapter(LlmAdapter):
        @property
        def provider_id(self):
            return "fake"

        async def stream(self, request):
            if False:
                yield

        async def close(self):
            closed.append("llm")

    class Mcp:
        errors = ()

        def __init__(self, *args):
            pass

        async def start(self):
            if fail:
                raise RuntimeError("初始化失败")

        async def discover(self):
            return ()

        async def close(self):
            closed.append("mcp")

    monkeypatch.setattr("camel_harness.agent.runtime._build_llm_adapter", lambda _: Adapter())
    monkeypatch.setattr("camel_harness.agent.runtime.McpManager", Mcp)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("resources", tmp_path, "资源测试")
    if fail:
        with pytest.raises(RuntimeError, match="初始化失败"):
            await build_agent(AgentSettings(), "resources", tmp_path, DenyApproval(), sessions)
    else:
        agent = await build_agent(AgentSettings(), "resources", tmp_path, DenyApproval(), sessions)
        await agent.close()
        await agent.close()
    assert closed == ["mcp", "llm"]


@pytest.mark.parametrize("enabled", [False, True])
async def test_factory_wires_memory_and_disabled_mode(tmp_path, monkeypatch, enabled):
    from pathlib import Path

    from camel_harness.agent.config import MemorySettings
    from camel_harness.agent.resources import MemoryEntry, MemoryStore
    from camel_harness.llm import LlmAdapter, LlmStopReason, MessageCompleted, TextDelta

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

    class Mcp:
        errors = ()

        def __init__(self, *args):
            pass

        async def start(self):
            pass

        async def discover(self):
            return ()

        async def close(self):
            pass

    monkeypatch.setattr("camel_harness.agent.runtime._build_llm_adapter", lambda _: Adapter())
    monkeypatch.setattr("camel_harness.agent.runtime.McpManager", Mcp)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("memory", tmp_path, "记忆组合")
    agent = await build_agent(
        AgentSettings(memory=MemorySettings(enabled=enabled)),
        "memory",
        tmp_path,
        DenyApproval(),
        sessions,
    )
    try:
        await agent.run("中文注释")
    finally:
        await agent.close()
    assert len(requests) == 1
    names = {tool.name for tool in requests[0].tools}
    memory_names = {"search_memory", "read_memory", "save_memory", "delete_memory"}
    assert names & memory_names == (memory_names if enabled else set())
    assert ("只有公开 API 需要 docstring" in requests[0].system_prompt) is enabled
    assert [entry.name for entry in await memories.list()] == ["language"]
    assert not (home / ".camel").exists()
