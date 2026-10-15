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


def _store(config: Path, **fields: object) -> SettingsStore:
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
    store = _store(config)

    await store.add_model(custom_model())
    snapshot = await store.snapshot()
    profile = snapshot["models"][0]  # type: ignore[index]
    model_id = str(profile["id"])  # type: ignore[index]
    assert profile["configured"] is True  # type: ignore[index]
    assert profile["has_api_key"] is True  # type: ignore[index]
    assert "custom-openai-key" not in json.dumps(snapshot)
    # 首个带密钥的模型自动成为默认
    assert snapshot["default_model_id"] == model_id
    assert stat.S_IMODE(config.stat().st_mode) == 0o600

    resolved = await store.resolve(model_id)
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-test"
    assert resolved.api_key == "custom-openai-key"
    assert resolved.base_url == "https://example.test/v1"
    assert (await store.resolve(None)).model == "gpt-test"

    await store.update_model(model_id, custom_model(api_key=None))
    assert (await store.resolve(model_id)).api_key == "custom-openai-key"
    await store.update_preferences(model_id)

    reloaded = SettingsStore(config)
    assert (await reloaded.snapshot())["default_model_id"] == model_id

    await reloaded.update_model(
        model_id, custom_model(api_key=None).model_copy(update={"clear_api_key": True})
    )
    with pytest.raises(ValueError, match="缺少 API Key"):
        await reloaded.resolve(model_id)


async def test_delete_model_rebinds_default_to_next_configured(tmp_path: Path) -> None:
    store = _store(tmp_path / "config.json")
    await store.add_model(custom_model())
    first = str((await store.snapshot())["models"][0]["id"])  # type: ignore[index]
    await store.add_model(
        ModelInput(name="备用", provider="anthropic", model="claude-test", api_key="k2")
    )
    second = str((await store.snapshot())["models"][1]["id"])  # type: ignore[index]
    assert (await store.snapshot())["default_model_id"] == first

    await store.delete_model(first)
    assert (await store.snapshot())["default_model_id"] == second
    with pytest.raises(KeyError):
        await store.delete_model(first)


class FakeAgent:
    def __init__(self) -> None:
        self.is_running = False
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_model_switch_reuses_agent_and_resolves_per_run(tmp_path: Path) -> None:
    """模型是 per-run 输入：切换不重建 Agent，两次解析产出不同 LlmModel。"""

    store = _store(tmp_path / "config.json")
    await store.add_model(custom_model())
    first_id = str((await store.snapshot())["models"][0]["id"])  # type: ignore[index]
    await store.add_model(
        ModelInput(name="备用", provider="anthropic", model="claude-test", api_key="k2")
    )
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("test-session", tmp_path / "workspace", "测试")
    agents: list[FakeAgent] = []

    async def build_agent(_: str, __: Path, ___: AgentSettings) -> Agent:
        agent = FakeAgent()
        agents.append(agent)
        return cast(Agent, agent)

    runs = RunService(build_agent, sessions, store)
    first = await runs.require_agent("test-session")

    async with runs.acquire("test-session", first_id) as (agent, model):
        assert agent is first
        assert model.model == "gpt-test"
    async with runs.acquire("test-session", None) as (agent, model):
        assert agent is first
        assert model.model == "gpt-test"
    assert len(agents) == 1
    assert not agents[0].closed

    agents[0].is_running = True
    session_service = SessionService(sessions, runs, store)
    with pytest.raises(AgentBusyError, match="切换会话模型"):
        await session_service.update("test-session", model_id=first_id)
    agents[0].is_running = False
    await runs.close()
    assert agents[0].closed


async def test_session_remembers_selected_model(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("test-session", tmp_path / "workspace", "测试")
    await sessions.update("test-session", model_id="custom-profile")
    reloaded = await sessions.get("test-session")
    assert reloaded is not None
    assert reloaded.model_id == "custom-profile"
