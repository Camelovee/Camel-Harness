from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from camel_harness.types import PermissionMode

from .agent_loop import AgentLoop, ContextLoader, MessageSink
from .events import AgentStreamEvent
from .options import AgentOptions


class Agent:
    """保存 Agent 默认配置，并把每次运行委托给低层循环。"""

    def __init__(self, options: AgentOptions, loop: AgentLoop) -> None:
        self._options = options
        self._loop = loop

    @property
    def options(self) -> AgentOptions:
        """返回后续 Turn 使用的默认选项。"""

        return self._options

    def update_options(self, options: AgentOptions) -> None:
        """替换后续 Turn 的默认选项，不影响已经启动的 Turn。"""

        self._options = options

    async def run(
        self,
        session_id: str,
        workspace: Path,
        system_prompt: str,
        load_context: ContextLoader,
        on_message: MessageSink,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        options: AgentOptions | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """使用当前配置启动低层 Agent Loop。"""

        async for event in self._loop.run(
            session_id=session_id,
            workspace=workspace,
            system_prompt=system_prompt,
            options=options or self._options,
            load_context=load_context,
            on_message=on_message,
            permission_mode=permission_mode,
        ):
            yield event
