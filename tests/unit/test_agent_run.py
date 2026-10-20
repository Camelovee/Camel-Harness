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
        ("max_output_tokens", 0),
        ("max_duration_seconds", 0),
    ),
)
def test_agent_options_reject_invalid_values(field: str, value: str | int) -> None:
    """AgentOptions 在进入运行循环前拒绝无效预算配置。"""

    values: dict[str, object] = {}
    values[field] = value
    with pytest.raises(ValueError):
        AgentOptions(**values)  # type: ignore[arg-type]
