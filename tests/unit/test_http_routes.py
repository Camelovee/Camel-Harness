from pathlib import Path
from typing import cast

import pytest

from camel_backend.api import HttpDependencies, create_router
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager


def test_http_router_keeps_public_endpoints_after_module_split() -> None:
    """HTTP 内部拆分不得改变浏览器依赖的公开路径。"""

    router = create_router(HttpDependencies(Path.cwd()))

    assert str(router.url_path_for("health")) == "/api/health"
    assert str(router.url_path_for("sessions")) == "/api/sessions"
    assert str(router.url_path_for("get_session", session_id="s")) == "/api/sessions/s"
    assert (
        str(router.url_path_for("session_messages", session_id="s")) == "/api/sessions/s/messages"
    )
    assert str(router.url_path_for("run_agent", session_id="s")) == "/api/sessions/s/runs"
    assert (
        str(router.url_path_for("steer_agent", session_id="s"))
        == "/api/sessions/s/runs/current/steer"
    )
    assert (
        str(router.url_path_for("abort_agent", session_id="s"))
        == "/api/sessions/s/runs/current/abort"
    )
    assert str(router.url_path_for("resume_agent", session_id="s")) == "/api/sessions/s/runs/resume"
    assert str(router.url_path_for("workspaces")) == "/api/workspaces"
    assert str(router.url_path_for("select_workspace")) == "/api/workspaces/select"
    assert str(router.url_path_for("approvals")) == "/api/approvals"
    assert str(router.url_path_for("resolve_approval", request_id="r")) == "/api/approvals/r"
    assert str(router.url_path_for("settings")) == "/api/settings"
    assert str(router.url_path_for("update_preferences")) == "/api/settings/preferences"
    assert str(router.url_path_for("add_model")) == "/api/settings/models"
    assert str(router.url_path_for("update_model", model_id="m")) == "/api/settings/models/m"


async def test_http_dependencies_are_published_as_one_complete_set() -> None:
    """启动期依赖必须整体发布和清除，不允许路由观察到半初始化状态。"""

    dependencies = HttpDependencies(Path.cwd())
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        await dependencies.require_agent("session-1")

    agent = cast(Agent, object())
    sessions = cast(SessionManager, object())

    async def build_agent(_: str, __: Path, ___: AgentSettings) -> Agent:
        return agent

    dependencies.bind(build_agent, sessions)
    assert dependencies.require_sessions() is sessions

    await dependencies.close()
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        await dependencies.require_agent("session-1")
