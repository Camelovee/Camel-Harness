from abc import ABC, abstractmethod

from .permission import PermissionDecision, PermissionRequest


class PermissionPolicy(ABC):
    """工具授权判断接口；策略本身不得产生环境副作用。"""

    @abstractmethod
    def decide(self, request: PermissionRequest) -> PermissionDecision:
        raise NotImplementedError
