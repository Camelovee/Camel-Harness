import json
import stat
from pathlib import Path
from typing import cast

import pytest

from camel_backend.orchestration import RunService, SessionService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent, AgentBusyError
from camel_harness.agent.config import AgentSettings, ModelInput
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


def _settings_store(config: Path, **fields: object) -> SettingsStore:
    """把测试配置写入临时 config.json 后构造 SettingsStore。"""

    config.write_text(json.dumps(fields), encoding="utf-8")
    return SettingsStore(config)


def custom_model(api_key: str | None = "custom-openai-key") -> ModelInput:
    return ModelInput(
        name="OpenAI 测试模型",
        provider="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key=api_key,
    )


async def test_settings_persist_models_to_config_without_exposing_keys(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    settings_store = _settings_store(config)

    await settings_store.add_model(custom_model())
    snapshot = await settings_store.snapshot()
    profile = snapshot["models"][0]  # type: ignore[index]
    model_id = str(profile["id"])  # type: ignore[index]
    assert profile["configured"] is True  # type: ignore[index]
    assert profile["has_api_key"] is True  # type: ignore[index]
    assert "custom-openai-key" not in json.dumps(snapshot)
    # 首个带密钥的模型自动成为默认
    assert snapshot["default_model_id"] == model_id
    assert stat.S_IMODE(config.stat().st_mode) == 0o600

    resolved = await settings_store.resolve(model_id)
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-test"
    assert resolved.api_key == "custom-openai-key"
    assert resolved.base_url == "https://example.test/v1"
    assert (await settings_store.resolve(None)).model == "gpt-test"

    await settings_store.update_model(model_id, custom_model(api_key=None))
    assert (await settings_store.resolve(model_id)).api_key == "custom-openai-key"
    await settings_store.update_preferences(model_id)

    reloaded_settings_store = SettingsStore(config)
    assert (await reloaded_settings_store.snapshot())["default_model_id"] == model_id

    await reloaded_settings_store.update_model(
        model_id, custom_model(api_key=None).model_copy(update={"clear_api_key": True})
    )
    with pytest.raises(ValueError, match="缺少 API Key"):
        await reloaded_settings_store.resolve(model_id)


async def test_delete_model_rebinds_default_to_next_configured(tmp_path: Path) -> None:
    settings_store = _settings_store(tmp_path / "config.json")
    await settings_store.add_model(custom_model())
    first = str((await settings_store.snapshot())["models"][0]["id"])  # type: ignore[index]
    await settings_store.add_model(
        ModelInput(name="备用", provider="anthropic", model="claude-test", api_key="k2")
    )
    second = str((await settings_store.snapshot())["models"][1]["id"])  # type: ignore[index]
    assert (await settings_store.snapshot())["default_model_id"] == first

    await settings_store.delete_model(first)
    assert (await settings_store.snapshot())["default_model_id"] == second
    with pytest.raises(KeyError):
        await settings_store.delete_model(first)


class FakeAgent:
    def __init__(self) -> None:
        self.is_running = False
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_model_switch_reuses_agent_and_resolves_per_run(tmp_path: Path) -> None:
    """模型是 per-run 输入：切换不重建 Agent，两次解析产出不同 LlmModel。"""

    settings_store = _settings_store(tmp_path / "config.json")
    await settings_store.add_model(custom_model())
    first_id = str((await settings_store.snapshot())["models"][0]["id"])  # type: ignore[index]
    await settings_store.add_model(
        ModelInput(name="备用", provider="anthropic", model="claude-test", api_key="k2")
    )
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("test-session", tmp_path / "workspace", "测试")
    agents: list[FakeAgent] = []

    async def build_agent(_: str, __: Path, agent_settings: AgentSettings) -> Agent:
        agent = FakeAgent()
        agents.append(agent)
        return cast(Agent, agent)

    run_service = RunService(
        build_agent=build_agent, session_manager=session_manager, settings_store=settings_store
    )
    first = await run_service.require_agent("test-session")

    async with run_service.acquire("test-session", first_id) as (agent, model):
        assert agent is first
        assert model.model == "gpt-test"
    async with run_service.acquire("test-session", None) as (agent, model):
        assert agent is first
        assert model.model == "gpt-test"
    assert len(agents) == 1
    assert not agents[0].closed

    agents[0].is_running = True
    session_service = SessionService(
        session_manager=session_manager, run_service=run_service, settings_store=settings_store
    )
    with pytest.raises(AgentBusyError, match="切换会话模型"):
        await session_service.update("test-session", model_id=first_id)
    agents[0].is_running = False
    await run_service.close()
    assert agents[0].closed


async def test_session_remembers_selected_model(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("test-session", tmp_path / "workspace", "测试")
    await session_manager.update("test-session", model_id="custom-profile")
    reloaded = await session_manager.get("test-session")
    assert reloaded is not None
    assert reloaded.model_id == "custom-profile"
