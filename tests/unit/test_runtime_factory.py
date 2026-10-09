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
