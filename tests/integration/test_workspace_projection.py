from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, install_exception_handlers
from camel_backend.api.routes.sessions import create_sessions_router
from camel_backend.api.routes.workspaces import _group_sessions, create_workspaces_router
from camel_backend.main import create_app
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


def _settings_store(config: Path, **fields: object) -> SettingsStore:
    """把测试配置写入临时 config.json 后构造 SettingsStore。"""

    import json

    config.write_text(json.dumps(fields), encoding="utf-8")
    return SettingsStore(config)


def _services(tmp_path: Path, session_manager: SessionManager) -> tuple[RunService, SettingsStore]:
    settings_store = _settings_store(tmp_path / "config.json")
    return RunService(
        build_agent=_stub_build_agent,
        session_manager=session_manager,
        settings_store=settings_store,
    ), settings_store


async def _stub_build_agent(_: str, __: Path, agent_settings: AgentSettings) -> Agent:
    raise AssertionError("该用例不应触发 Agent 创建")


async def test_workspaces_are_projected_from_session_directories(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    await session_manager.create("first-session", first_workspace, "第一个会话")
    await session_manager.create("second-session", first_workspace, "第二个会话")
    await session_manager.create("third-session", second_workspace, "第三个会话")
    await session_manager.create(
        "sub-session", first_workspace, "子会话", parent_session_id="first-session"
    )

    result = _group_sessions(await session_manager.list())

    grouped = dict(result)
    assert {item.session_id for item in grouped[str(first_workspace.resolve())]} == {
        "first-session",
        "second-session",
    }
    assert [item.session_id for item in grouped[str(second_workspace.resolve())]] == [
        "third-session"
    ]


async def test_session_api_hides_children_and_deletes_them_with_parent(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    await session_manager.create("child", tmp_path, "子会话", parent_session_id="parent")
    run_service, settings_store = _services(tmp_path, session_manager)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        run_service=run_service,
        session_manager=session_manager,
        session_service=SessionService(
            session_manager=session_manager, run_service=run_service, settings_store=settings_store
        ),
        settings_service=SettingsService(
            settings_store=settings_store, session_manager=session_manager, run_service=run_service
        ),
        settings_store=settings_store,
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_sessions_router(dependencies))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/sessions")
        assert listed.status_code == 200
        assert [item["session_id"] for item in listed.json()["sessions"]] == ["parent"]

        missing = await client.get("/sessions/unknown/messages")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "会话不存在"

        deleted = await client.delete("/sessions/parent")
        assert deleted.status_code == 204
        assert await session_manager.get("child") is None


async def test_workspace_selection_uses_injected_platform_function(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    selected = tmp_path / "selected"

    async def select_workspace() -> Path | None:
        return selected

    run_service, settings_store = _services(tmp_path, session_manager)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        run_service=run_service,
        session_manager=session_manager,
        session_service=SessionService(
            session_manager=session_manager, run_service=run_service, settings_store=settings_store
        ),
        settings_service=SettingsService(
            settings_store=settings_store, session_manager=session_manager, run_service=run_service
        ),
        settings_store=settings_store,
        select_workspace=select_workspace,
    )

    assert await dependencies.select_workspace() == selected


async def test_new_chat_without_workspace_uses_local_default(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    run_service, settings_store = _services(tmp_path, session_manager)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        run_service=run_service,
        session_manager=session_manager,
        session_service=SessionService(
            session_manager=session_manager, run_service=run_service, settings_store=settings_store
        ),
        settings_service=SettingsService(
            settings_store=settings_store, session_manager=session_manager, run_service=run_service
        ),
        settings_store=settings_store,
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_workspaces_router(dependencies))
    app.include_router(create_sessions_router(dependencies))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        default = await client.get("/workspaces/default")
        assert default.status_code == 200
        assert default.json()["workspace"] == str(tmp_path.resolve())

        created = await client.post("/sessions", json={"name": "新对话"})
        assert created.status_code == 201
        assert created.json()["workspace"] == str(tmp_path.resolve())


async def test_web_app_defaults_to_user_home() -> None:
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        default = await client.get("/api/workspaces/default")

    assert default.status_code == 200
    assert default.json()["workspace"] == str(Path.home().resolve())
