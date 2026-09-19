import pytest

from camel_harness import AgentOptions, AgentRun, RunStatus, StopReason


def test_agent_run_enforces_turn_limit() -> None:
    run = AgentRun("session", "run", max_turns=1)
    run.start()

    assert run.next_turn() == 1
    with pytest.raises(RuntimeError, match="最大 Turn"):
        run.next_turn()

    assert run.status is RunStatus.FAILED
    assert run.stop_reason is StopReason.MAX_TURNS


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", ""),
        ("max_output_tokens", 0),
        ("max_turns", 0),
        ("max_total_tokens", 0),
        ("max_duration_seconds", 0),
    ),
)
def test_agent_options_reject_invalid_values(field: str, value: str | int) -> None:
    """AgentOptions 在进入运行循环前拒绝无效模型与预算配置。"""

    values: dict[str, object] = {"model": "fake-model"}
    values[field] = value
    with pytest.raises(ValueError):
        AgentOptions(**values)  # type: ignore[arg-type]


def test_agent_options_do_not_duplicate_bound_llm_provider() -> None:
    """Provider 由 LlmClient 绑定，不在 AgentOptions 中维护第二份状态。"""

    assert not hasattr(AgentOptions("fake-model"), "provider")
