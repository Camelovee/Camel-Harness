from __future__ import annotations

from abc import ABC, abstractmethod

from .subagent import SubagentRequest, SubagentResult


class SubagentRunner(ABC):
    """执行隔离 Agent 委派的可替换运行器。"""

    @abstractmethod
    async def run(self, request: SubagentRequest) -> SubagentResult:
        raise NotImplementedError
