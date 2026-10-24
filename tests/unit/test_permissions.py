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


def test_accept_edits_still_asks_for_shell() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.ACCEPT_EDITS))
    assert decision.action is PermissionAction.ASK


def test_yolo_allows_execute() -> None:
    decision = default_permission_decision(_request("execute", PermissionMode.YOLO))
    assert decision.action is PermissionAction.ALLOW


def test_memory_writes_are_auto_allowed_except_plan() -> None:
    def memory_request(mode: PermissionMode) -> PermissionRequest:
        return PermissionRequest("memory", frozenset({"memory.write"}), "write", {}, mode)

    for mode in (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.YOLO):
        assert default_permission_decision(memory_request(mode)).action is PermissionAction.ALLOW
    assert default_permission_decision(memory_request(PermissionMode.PLAN)).action is (
        PermissionAction.DENY
    )
