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
