import json
import stat
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, create_router, install_exception_handlers
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


def _settings_store(config: Path, **fields: object) -> SettingsStore:
    """把测试配置写入临时 config.json 后构造 SettingsStore。"""

    config.write_text(json.dumps(fields), encoding="utf-8")
    return SettingsStore(config)


async def test_settings_api_and_session_model_selection(tmp_path: Path) -> None:
    """浏览器只能看到密钥状态，会话选择须真实写入元数据。"""

    path = tmp_path / "config.json"
    settings_store = _settings_store(
        path,
        models=[
            {
                "id": "m1",
                "name": "主模型",
                "provider": "anthropic",
                "model": "claude-test",
                "api_key": "main-secret",
            }
        ],
        default_model_id="m1",
        max_output_tokens=1234,
        compaction={"max_output_tokens": 6000},
        memory={"enabled": False, "context_tokens": 300},
    )
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("session-one", tmp_path / "workspace", "测试会话")

    async def build_agent(_: str, __: Path, agent_settings: AgentSettings) -> Agent:
        raise AssertionError("该用例不应触发 Agent 创建")

    runs = RunService(
        build_agent=build_agent, session_manager=session_manager, settings_store=settings_store
    )
    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(
        run_service=runs,
        session_manager=session_manager,
        session_service=SessionService(
            session_manager=session_manager, run_service=runs, settings_store=settings_store
        ),
        settings_service=SettingsService(
            settings_store=settings_store, session_manager=session_manager, run_service=runs
        ),
        settings_store=settings_store,
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_router(dependencies))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        snapshot = await client.get("/api/settings")
        assert snapshot.status_code == 200
        assert "main-secret" not in snapshot.text
        assert snapshot.json()["models"][0]["configured"] is True
        assert snapshot.json()["default_model_id"] == "m1"

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
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        profile_id = created.json()["models"][1]["id"]
        # 已有可用默认模型，不自动改绑
        assert created.json()["default_model_id"] == "m1"
        assert AgentSettings.load(path).models[1].api_key == "private-test-key"

        # 编辑普通字段不能抹掉未回显的密钥
        updated = await client.put(
            f"/api/settings/models/{profile_id}",
            json={"name": "OpenAI 改名", "provider": "openai", "model": "gpt-test-2"},
        )
        assert updated.status_code == 200
        assert updated.json()["models"][1]["name"] == "OpenAI 改名"
        assert AgentSettings.load(path).models[1].api_key == "private-test-key"
        assert AgentSettings.load(path).models[1].model == "gpt-test-2"

        before = path.read_bytes()
        invalid = await client.put(
            f"/api/settings/models/{profile_id}",
            json={"name": "OpenAI 改名", "provider": "openai", "model": "", "api_key": "x"},
        )
        assert invalid.status_code == 422
        assert path.read_bytes() == before

        selected = await client.patch("/api/sessions/session-one", json={"model_id": profile_id})
        assert selected.status_code == 200
        assert selected.json()["model_id"] == profile_id
        assert (await session_manager.get("session-one")).model_id == profile_id  # type: ignore[union-attr]

        renamed = await client.patch("/api/sessions/session-one", json={"name": "重命名后的会话"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "重命名后的会话"
        assert (await session_manager.get("session-one")).name == "重命名后的会话"  # type: ignore[union-attr]

        invalid_name = await client.patch("/api/sessions/session-one", json={"name": "   "})
        assert invalid_name.status_code == 422

        invalid = await client.patch("/api/sessions/session-one", json={"model_id": "not-found"})
        assert invalid.status_code == 422

        deleted = await client.delete(f"/api/settings/models/{profile_id}")
        assert deleted.status_code == 204
        # 删除后使用它的会话改绑到默认模型
        assert (await session_manager.get("session-one")).model_id == "m1"  # type: ignore[union-attr]

    await dependencies.close()
