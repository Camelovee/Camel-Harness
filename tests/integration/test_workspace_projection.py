from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, install_exception_handlers
from camel_backend.api.routes.sessions import create_sessions_router
from camel_backend.api.routes.workspaces import _group_sessions
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


def _services(tmp_path: Path, sessions: SessionManager) -> tuple[RunService, WebSettingsStore]:
    store = WebSettingsStore(tmp_path / "web-settings.json", AgentSettings())
    return RunService(_stub_build_agent, sessions, store), store


async def _stub_build_agent(_: str, __: Path, ___: AgentSettings) -> Agent:
    raise AssertionError("该用例不应触发 Agent 创建")


async def test_workspaces_are_projected_from_session_directories(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    await sessions.create("first-session", first_workspace, "第一个会话")
    await sessions.create("second-session", first_workspace, "第二个会话")
    await sessions.create("third-session", second_workspace, "第三个会话")
    await sessions.create(
        "sub-session", first_workspace, "子会话", parent_session_id="first-session"
    )

    result = _group_sessions(await sessions.list())

    grouped = dict(result)
    assert {item.session_id for item in grouped[str(first_workspace.resolve())]} == {
        "first-session",
        "second-session",
    }
    assert [item.session_id for item in grouped[str(second_workspace.resolve())]] == [
        "third-session"
    ]


async def test_session_api_hides_children_and_deletes_them_with_parent(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    await sessions.create("child", tmp_path, "子会话", parent_session_id="parent")
    runs, store = _services(tmp_path, sessions)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        runs=runs,
        sessions=sessions,
        session_service=SessionService(sessions, runs, store),
        settings_service=SettingsService(store, sessions, runs),
        settings=store,
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_sessions_router(dependencies))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/sessions")
        assert listed.status_code == 200
        assert [item["session_id"] for item in listed.json()["sessions"]] == ["parent"]

        deleted = await client.delete("/sessions/parent")
        assert deleted.status_code == 204
        assert await sessions.get("child") is None


async def test_workspace_selection_uses_injected_platform_function(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    selected = tmp_path / "selected"

    async def select_workspace() -> Path | None:
        return selected

    runs, store = _services(tmp_path, sessions)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        runs=runs,
        sessions=sessions,
        session_service=SessionService(sessions, runs, store),
        settings_service=SettingsService(store, sessions, runs),
        settings=store,
        select_workspace=select_workspace,
    )

    assert await dependencies.select_workspace() == selected
