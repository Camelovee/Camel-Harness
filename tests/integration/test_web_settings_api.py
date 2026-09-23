from pathlib import Path
from typing import cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, create_router
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
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(cast(Agent, object()), sessions, settings=store)
    app = FastAPI()
    app.include_router(create_router(dependencies))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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

        selected = await client.patch(
            "/api/sessions/session-one", json={"model_id": profile_id}
        )
        assert selected.status_code == 200
        assert selected.json()["model_id"] == profile_id
        assert (await sessions.get("session-one")).model_id == profile_id  # type: ignore[union-attr]

        invalid = await client.patch(
            "/api/sessions/session-one", json={"model_id": "not-found"}
        )
        assert invalid.status_code == 422

        deleted = await client.delete(f"/api/settings/models/{profile_id}")
        assert deleted.status_code == 204
        assert (await sessions.get("session-one")).model_id == "environment"  # type: ignore[union-attr]

    await dependencies.close()
