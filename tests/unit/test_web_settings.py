import json
import stat
from pathlib import Path
from typing import cast

import pytest

from camel_backend.api import HttpDependencies
from camel_backend.web_settings import ModelInput, WebSettingsStore
from camel_harness.agent import Agent, AgentBusyError
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage


def environment(tmp_path: Path) -> AgentSettings:
    return AgentSettings.model_construct(
        provider="anthropic",
        model="claude-test",
        anthropic_api_key="env-anthropic-key",
        openai_api_key=None,
        session_dir=tmp_path / "sessions",
    )


def custom_model(api_key: str | None = "custom-openai-key") -> ModelInput:
    return ModelInput(
        name="OpenAI 测试模型",
        provider="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key=api_key,
    )


async def test_web_settings_persist_models_without_exposing_keys(tmp_path: Path) -> None:
    path = tmp_path / "web-settings.json"
    store = WebSettingsStore(path, environment(tmp_path))

    await store.add_model(custom_model())
    snapshot = await store.snapshot()
    profile = snapshot["models"][1]  # type: ignore[index]
    model_id = profile["id"]  # type: ignore[index]
    assert profile["configured"] is True  # type: ignore[index]
    assert profile["has_api_key"] is True  # type: ignore[index]
    assert "custom-openai-key" not in json.dumps(snapshot)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    selected_id, resolved = await store.resolve(str(model_id))
    assert selected_id == model_id
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-test"
    assert resolved.openai_api_key == "custom-openai-key"
    assert resolved.openai_base_url == "https://example.test/v1"

    await store.update_model(str(model_id), custom_model(api_key=None))
    assert (await store.resolve(str(model_id)))[1].openai_api_key == "custom-openai-key"
    await store.update_preferences(str(model_id), True)

    reloaded = WebSettingsStore(path, environment(tmp_path))
    assert (await reloaded.snapshot())["thinking"] is True
    assert (await reloaded.snapshot())["default_model_id"] == model_id
    assert (await reloaded.resolve(None))[1].model == "gpt-test"

    await reloaded.update_model(
        str(model_id), custom_model(api_key=None).model_copy(update={"clear_api_key": True})
    )
    with pytest.raises(ValueError, match="缺少 API Key"):
        await reloaded.resolve(str(model_id))


class FakeAgent:
    def __init__(self) -> None:
        self.is_running = False
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_session_agent_rebuilds_for_another_model_only_when_idle(tmp_path: Path) -> None:
    settings = environment(tmp_path)
    store = WebSettingsStore(tmp_path / "web-settings.json", settings)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("test-session", tmp_path / "workspace", "测试")
    agents: list[FakeAgent] = []
    built_models: list[str] = []

    async def build_agent(_: str, __: Path, selected: AgentSettings) -> Agent:
        built_models.append(selected.model)
        agent = FakeAgent()
        agents.append(agent)
        return cast(Agent, agent)

    dependencies = HttpDependencies(tmp_path)
    dependencies.bind(build_agent, sessions, settings=store)
    first = await dependencies.require_agent("test-session")
    assert await dependencies.require_agent("test-session") is first

    await store.add_model(custom_model())
    profile = (await store.snapshot())["models"][1]  # type: ignore[index]
    second = await dependencies.require_agent("test-session", str(profile["id"]))  # type: ignore[index]
    assert second is not first
    assert agents[0].closed
    assert built_models == ["claude-test", "gpt-test"]

    agents[1].is_running = True
    with pytest.raises(AgentBusyError, match="切换模型"):
        await dependencies.require_agent("test-session", "environment")
    with pytest.raises(AgentBusyError, match="切换会话模型"):
        await dependencies.update_session("test-session", model_id="environment")
    agents[1].is_running = False
    await dependencies.close()


async def test_session_remembers_selected_model(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("test-session", tmp_path / "workspace", "测试")
    await sessions.update("test-session", model_id="custom-profile")
    reloaded = await sessions.get("test-session")
    assert reloaded is not None
    assert reloaded.model_id == "custom-profile"
