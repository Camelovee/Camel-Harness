from camel_harness.tools import (
    PermissionAction,
    PermissionRequest,
    default_permission_decision,
)
from camel_harness.types import PermissionMode


def _request(risk: str, mode: PermissionMode) -> PermissionRequest:
    return PermissionRequest("tool", frozenset(), risk, {}, mode)


def test_plan_mode_denies_write() -> None:
    decision = default_permission_decision(_request("write", PermissionMode.PLAN))
    assert decision.action is PermissionAction.DENY


def test_accept_edits_still_asks_for_shell() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.ACCEPT_EDITS))
    assert decision.action is PermissionAction.ASK


def test_yolo_allows_execute() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.YOLO))
    assert decision.action is PermissionAction.ALLOW
