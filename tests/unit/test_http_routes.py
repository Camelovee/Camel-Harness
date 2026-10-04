from pathlib import Path
from typing import cast

import pytest

from camel_backend.api import HttpDependencies, create_router
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


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
    assert str(router.url_path_for("default_workspace")) == "/api/workspaces/default"
    assert str(router.url_path_for("select_workspace")) == "/api/workspaces/select"
    assert str(router.url_path_for("approvals")) == "/api/approvals"
    assert str(router.url_path_for("resolve_approval", request_id="r")) == "/api/approvals/r"
    assert str(router.url_path_for("settings")) == "/api/settings"
    assert str(router.url_path_for("update_preferences")) == "/api/settings/preferences"
    assert str(router.url_path_for("add_model")) == "/api/settings/models"
    assert str(router.url_path_for("update_model", model_id="m")) == "/api/settings/models/m"


async def test_http_dependencies_are_published_as_one_complete_set(tmp_path: Path) -> None:
    """启动期依赖必须整体发布和清除，不允许路由观察到半初始化状态。"""

    dependencies = HttpDependencies(tmp_path)
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        dependencies.require_runs()

    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    store = WebSettingsStore(tmp_path / "web-settings.json", AgentSettings())

    async def build_agent(_: str, __: Path, ___: AgentSettings) -> Agent:
        return cast(Agent, object())

    runs = RunService(build_agent, sessions, store)
    dependencies.bind(
        runs=runs,
        sessions=sessions,
        session_service=SessionService(sessions, runs, store),
        settings_service=SettingsService(store, sessions, runs),
        settings=store,
    )
    assert dependencies.require_runs() is runs
    assert dependencies.require_sessions() is sessions

    await dependencies.close()
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        dependencies.require_runs()
