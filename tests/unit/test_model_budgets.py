import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from camel_harness.agent.config import AgentSettings, ModelInput, ModelProfile
from camel_harness.agent.context.output_budget import (
    ContextBudgetError,
    context_input_limit,
    resolve_output_budget,
)
from camel_harness.agent.session import ContextNode, ContextSnapshot
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStreamEvent,
    LlmThinkingMode,
    StreamFailed,
    UserMessage,
)


def test_old_model_budgets_migrate_and_explicit_values_win(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    common = {"id": "a", "name": "模型", "provider": "openai", "model": "test", "api_key": "secret"}
    path.write_text(
        json.dumps(
            {
                "max_output_tokens": 4096,
                "compaction": {"context_window_tokens": 262144},
                "models": [
                    common,
                    {
                        **common,
                        "id": "b",
                        "context_window_tokens": 128000,
                        "max_output_tokens": 16384,
                        "thinking_mode": "openai_reasoning",
                    },
                ],
                "default_model_id": "a",
            }
        )
    )
    settings = AgentSettings.load(path)
    first, second = settings.models
    assert (first.context_window_tokens, first.max_output_tokens) == (262144, 4096)
    assert first.thinking_mode is LlmThinkingMode.PROVIDER_DEFAULT
    assert (second.context_window_tokens, second.max_output_tokens) == (128000, 16384)
    assert second.thinking_mode is LlmThinkingMode.OPENAI_REASONING
    assert settings.resolve_model(None).context_window_tokens == 262144
    assert settings.resolve_model("b").max_output_tokens == 16384
    settings.save(path)
    saved = json.loads(path.read_text())
    assert "max_output_tokens" not in saved
    assert "context_window_tokens" not in saved["compaction"]
    assert saved["models"][0]["api_key"] == "secret"
    assert AgentSettings.load(path) == settings


@pytest.mark.parametrize("model_type", [ModelInput, ModelProfile])
@pytest.mark.parametrize(
    "provider,mode",
    [
        ("openai", "anthropic_adaptive"),
        ("anthropic", "openai_reasoning"),
    ],
)
def test_model_thinking_mode_must_match_provider(
    model_type: type[ModelInput] | type[ModelProfile], provider: str, mode: str
) -> None:
    fields = {"name": "模型", "model": "test", "provider": provider, "thinking_mode": mode}
    if model_type is ModelProfile:
        fields["id"] = "a"
    with pytest.raises(ValueError, match="Provider"):
        model_type.model_validate(fields)
    with pytest.raises(ValueError, match="Provider"):
        LlmModel(provider, "test", thinking_mode=LlmThinkingMode(mode))


def test_output_budget_caps_reserves_and_rejects_exhaustion() -> None:
    model = LlmModel("fake", "m", context_window_tokens=32768, max_output_tokens=16384)
    assert resolve_output_budget(model, 100) == 16384
    assert resolve_output_budget(model, 20000) == 8672
    assert resolve_output_budget(model, 20000, 4096) == 4096
    assert resolve_output_budget(model, 28671) == 1
    with pytest.raises(ContextBudgetError, match="没有输出空间"):
        resolve_output_budget(model, 28672)
    assert context_input_limit(model, 8192) == 12288
    assert context_input_limit(model, 24000) == 8768
    with pytest.raises(ValueError, match="安全余量"):
        replace(model, context_window_tokens=20480)


async def test_selected_model_controls_capacity_even_when_compaction_disabled() -> None:
    from camel_harness.agent.config import CompactionSettings
    from camel_harness.agent.context import CompactionEngine, ContextManager
    from camel_harness.agent.context.compaction import CompactionError

    class NoRequestsLlm(LlmAdapter):
        calls: int = 0

        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.calls += 1
            yield StreamFailed("容量保护不应请求模型")

    adapter = NoRequestsLlm()
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(LlmClient(adapter), CompactionSettings(enabled=False)),
        system_prompt="基础",
    )
    snapshot = ContextSnapshot(nodes=(ContextNode("u", UserMessage("长" * 20000)),))
    large = LlmModel("fake", "m", context_window_tokens=262144)
    small = replace(large, context_window_tokens=32768)
    assert await context_manager.compact_if_needed(snapshot, large) is None
    with pytest.raises(CompactionError, match="未开启"):
        await context_manager.compact_if_needed(snapshot, small)
    assert adapter.calls == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"max_output_tokens": 0},
        {"context_window_tokens": 0},
        {"context_window_tokens": 20480, "max_output_tokens": 16384},
    ],
)
def test_invalid_model_capacities_are_rejected(fields: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        LlmModel("fake", "m", **fields)
    with pytest.raises(ValueError):
        ModelInput(name="模型", provider="openai", model="m", **fields)


@pytest.mark.parametrize(
    "legacy",
    [
        {"max_output_tokens": 0},
        {"compaction": {"context_window_tokens": 0}},
    ],
)
def test_invalid_legacy_budget_is_not_discarded_even_without_models(
    legacy: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        AgentSettings.model_validate(legacy)
