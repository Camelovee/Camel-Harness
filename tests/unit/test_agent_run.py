import pytest

from camel_harness import AgentOptions, AgentRun, RunStatus, StopReason


def test_agent_run_enforces_step_limit() -> None:
    run = AgentRun("session", "turn", max_steps=1)
    run.start()

    assert run.next_step() == 1
    with pytest.raises(RuntimeError, match="最大 Step"):
        run.next_step()

    assert run.status is RunStatus.FAILED
    assert run.stop_reason is StopReason.MAX_STEPS


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider", ""),
        ("model", ""),
        ("max_output_tokens", 0),
        ("max_steps", 0),
        ("max_total_tokens", 0),
        ("max_duration_seconds", 0),
    ),
)
def test_agent_options_reject_invalid_values(field: str, value: str | int) -> None:
    """AgentOptions 在进入运行循环前拒绝无效模型与预算配置。"""

    values: dict[str, object] = {"provider": "fake", "model": "fake-model"}
    values[field] = value
    with pytest.raises(ValueError):
        AgentOptions(**values)  # type: ignore[arg-type]
