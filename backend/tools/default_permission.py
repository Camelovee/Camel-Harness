from backend.types import PermissionMode

from .permission import PermissionAction, PermissionDecision, PermissionRequest
from .permission_policy import PermissionPolicy


class DefaultPermissionPolicy(PermissionPolicy):
    """提供保守且可预测的本地工具授权默认策略。"""

    def decide(self, request: PermissionRequest) -> PermissionDecision:
        if request.mode is PermissionMode.YOLO:
            return PermissionDecision(PermissionAction.ALLOW, "YOLO 模式允许所有已注册工具")
        if request.risk == "read":
            return PermissionDecision(PermissionAction.ALLOW, "只读能力自动允许")
        if request.mode is PermissionMode.PLAN:
            return PermissionDecision(PermissionAction.DENY, "Plan Mode 禁止产生副作用")
        if request.mode is PermissionMode.ACCEPT_EDITS and request.risk == "write":
            return PermissionDecision(PermissionAction.ALLOW, "接受编辑模式允许工作区写入")
        return PermissionDecision(PermissionAction.ASK, "该工具可能修改环境，需要用户确认")
