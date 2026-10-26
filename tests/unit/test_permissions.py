import pytest
from pydantic import ValidationError

from camel_backend.api.contracts import ResumeBody, RunBody
from camel_harness.agent.tools import (
    PermissionAction,
    PermissionMode,
    PermissionRequest,
    default_permission_decision,
)


def _request(risk: str, mode: PermissionMode) -> PermissionRequest:
    return PermissionRequest("tool", frozenset(), risk, {}, mode)


def test_plan_mode_denies_write() -> None:
    decision = default_permission_decision(_request("write", PermissionMode.PLAN))
    assert decision.action is PermissionAction.DENY


def test_default_asks_for_shell() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.DEFAULT))
    assert decision.action is PermissionAction.ASK


def test_default_asks_for_workspace_writes() -> None:
    decision = default_permission_decision(_request("write", PermissionMode.DEFAULT))
    assert decision.action is PermissionAction.ASK


@pytest.mark.parametrize("body_type", [RunBody, ResumeBody])
def test_removed_permission_mode_is_rejected(body_type: type[RunBody | ResumeBody]) -> None:
    with pytest.raises(ValidationError):
        body_type.model_validate({"prompt": "任务", "permission_mode": "accept_edits"})
    with pytest.raises(ValueError):
        PermissionMode("accept_edits")


def test_yolo_allows_execute() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.YOLO))
    assert decision.action is PermissionAction.ALLOW


def test_memory_writes_are_auto_allowed_except_plan() -> None:
    def memory_request(mode: PermissionMode) -> PermissionRequest:
        return PermissionRequest("memory", frozenset({"memory.write"}), "write", {}, mode)

    for mode in (PermissionMode.DEFAULT, PermissionMode.YOLO):
        assert default_permission_decision(memory_request(mode)).action is PermissionAction.ALLOW
    assert default_permission_decision(memory_request(PermissionMode.PLAN)).action is (
        PermissionAction.DENY
    )
