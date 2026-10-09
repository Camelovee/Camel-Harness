import json
import stat
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, create_router, install_exception_handlers
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


async def test_settings_api_and_session_model_selection(tmp_path: Path) -> None:
    """浏览器只能看到密钥状态，会话选择须真实写入元数据。"""

    settings = AgentSettings.model_construct(
        provider="anthropic",
        model="claude-test",
        anthropic_api_key=None,
        openai_api_key=None,
        session_dir=tmp_path / "sessions",
    )
    store = WebSettingsStore(tmp_path / "web-settings.json", settings)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("session-one", tmp_path / "workspace", "测试会话")

    async def build_agent(_: str, __: Path, ___: AgentSettings) -> Agent:
        raise AssertionError("该用例不应触发 Agent 创建")

    runs = RunService(build_agent, sessions, store)
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        runs=runs,
        sessions=sessions,
        session_service=SessionService(sessions, runs, store),
        settings_service=SettingsService(
            store, sessions, runs, config_path=tmp_path / "config.json"
        ),
        settings=store,
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_router(dependencies))
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "max_output_tokens": 1234,
                "openai_api_key": "other-secret",
                "compaction": {"max_output_tokens": 6000},
            }
        ),
        encoding="utf-8",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        config = await client.get("/api/settings/config")
        assert config.status_code == 200
        draft = config.json()
        saved = await client.put(
            "/api/settings/config",
            json={
                **draft,
                "model": "custom-default",
                "base_url": "https://example.test/anthropic",
                "api_key": "config-secret",
            },
        )
        assert saved.status_code == 200
        assert "config-secret" not in saved.text
        assert saved.json()["keys_configured"]["anthropic"] is True
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert AgentSettings.load(path).anthropic_api_key == "config-secret"
        assert AgentSettings.load(path).openai_api_key == "other-secret"
        assert AgentSettings.load(path).max_output_tokens == 1234
        assert AgentSettings.load(path).compaction.max_output_tokens == 6000
        assert AgentSettings.load(path).model == "custom-default"
        assert AgentSettings.load(path).anthropic_base_url == "https://example.test/anthropic"

        # 编辑普通字段不能抹掉未回显的密钥。
        saved_again = await client.put("/api/settings/config", json=saved.json())
        assert saved_again.status_code == 200
        assert AgentSettings.load(path).anthropic_api_key == "config-secret"
        before = path.read_bytes()
        invalid = await client.put(
            "/api/settings/config",
            json={
                **saved_again.json(),
                "model": "",
                "api_key": "private-secret",
            },
        )
        assert invalid.status_code == 422
        assert "private-secret" not in invalid.text
        assert path.read_bytes() == before
        stale = await client.put("/api/settings/config", json=draft)
        assert stale.status_code == 422
        assert "重新加载" in stale.text
        cleared = await client.put(
            "/api/settings/config",
            json={
                **saved_again.json(),
                "clear_api_key": True,
            },
        )
        assert cleared.status_code == 200
        assert AgentSettings.load(path).anthropic_api_key is None
        switched = await client.put(
            "/api/settings/config",
            json={
                **cleared.json(),
                "provider": "openai",
                "model": "openai-default",
                "base_url": None,
            },
        )
        assert switched.status_code == 200
        assert "other-secret" not in switched.text
        assert AgentSettings.load(path).provider == "openai"
        assert AgentSettings.load(path).openai_api_key == "other-secret"

        created = await client.post(
            "/api/settings/models",
            json={
                "name": "OpenAI 测试",
                "provider": "openai",
                "model": "gpt-test",
                "api_key": "private-test-key",
            },
        )
        assert created.status_code == 201
        assert "private-test-key" not in created.text
        profile_id = created.json()["models"][1]["id"]
        assert created.json()["default_model_id"] == profile_id

        selected = await client.patch("/api/sessions/session-one", json={"model_id": profile_id})
        assert selected.status_code == 200
        assert selected.json()["model_id"] == profile_id
        assert (await sessions.get("session-one")).model_id == profile_id  # type: ignore[union-attr]

        renamed = await client.patch("/api/sessions/session-one", json={"name": "重命名后的会话"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "重命名后的会话"
        assert (await sessions.get("session-one")).name == "重命名后的会话"  # type: ignore[union-attr]

        invalid_name = await client.patch("/api/sessions/session-one", json={"name": "   "})
        assert invalid_name.status_code == 422

        invalid = await client.patch("/api/sessions/session-one", json={"model_id": "not-found"})
        assert invalid.status_code == 422

        deleted = await client.delete(f"/api/settings/models/{profile_id}")
        assert deleted.status_code == 204
        assert (await sessions.get("session-one")).model_id == "environment"  # type: ignore[union-attr]

    await dependencies.close()
