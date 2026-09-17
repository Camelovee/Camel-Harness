from backend.tools import (
    DefaultPermissionPolicy,
    PermissionAction,
    PermissionRequest,
)
from backend.types import PermissionMode


def _request(risk: str, mode: PermissionMode) -> PermissionRequest:
    return PermissionRequest("tool", frozenset(), risk, {}, mode)


def test_plan_mode_denies_write() -> None:
    decision = DefaultPermissionPolicy().decide(_request("write", PermissionMode.PLAN))
    assert decision.action is PermissionAction.DENY


def test_accept_edits_still_asks_for_shell() -> None:
    decision = DefaultPermissionPolicy().decide(_request("execute", PermissionMode.ACCEPT_EDITS))
    assert decision.action is PermissionAction.ASK


def test_yolo_allows_execute() -> None:
    decision = DefaultPermissionPolicy().decide(_request("execute", PermissionMode.YOLO))
    assert decision.action is PermissionAction.ALLOW
