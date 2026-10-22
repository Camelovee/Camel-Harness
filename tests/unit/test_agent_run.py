import pytest

from camel_harness import AgentOptions, AgentRun, RunStatus, StopReason


def test_agent_run_enforces_turn_limit() -> None:
    agent_run = AgentRun("session", "run", max_turns=1)
    agent_run.start()

    assert agent_run.next_turn() == 1
    with pytest.raises(RuntimeError, match="最大 Turn"):
        agent_run.next_turn()

    assert agent_run.status is RunStatus.FAILED
    assert agent_run.stop_reason is StopReason.MAX_TURNS


@pytest.mark.parametrize(
    ("field", "value"),
    (("max_duration_seconds", 0),),
)
def test_agent_options_reject_invalid_values(field: str, value: str | int) -> None:
    """AgentOptions 在进入运行循环前拒绝无效预算配置。"""

    values: dict[str, object] = {}
    values[field] = value
    with pytest.raises(ValueError):
        AgentOptions(**values)  # type: ignore[arg-type]
