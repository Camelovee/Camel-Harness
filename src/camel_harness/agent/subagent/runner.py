from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from camel_harness.llm import AssistantMessage, TextBlock

from ..events import AgentEndEvent, AgentEvent, agent_event_to_dict
from ..session import SessionManager
from ..session.types import TaskOutcome
from ..tools import PermissionMode
from ..tools.file_result_storage import ToolResultStorageError
from .types import SubagentBuilder, SubagentRequest, SubagentResult, SubagentType


class LocalSubagentRunner:
    """管理子 Agent 的执行、观察、清理与终态保存，创建过程由组装层注入。"""

    def __init__(
        self,
        build_subagent: SubagentBuilder,
        session_manager: SessionManager,
    ) -> None:
        self._build_subagent = build_subagent
        self._session_manager = session_manager

    async def run(self, subagent_request: SubagentRequest) -> SubagentResult:
        agent = await self._build_subagent(subagent_request)
        session_id = agent.session_id
        # 非 general 强制 PLAN，是工具白名单之外的第二道兜底。
        permission_mode = (
            subagent_request.permission_mode
            if subagent_request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )

        content = ""
        succeeded = False
        error = "子 Agent 未产生终态"

        terminal: AgentEndEvent | None = None

        def collect_text(agent_event: AgentEvent) -> None:
            nonlocal content, succeeded, error, terminal
            if agent_event.type == "message_end" and isinstance(
                agent_event.message, AssistantMessage
            ):
                content = "".join(
                    block.text
                    for block in agent_event.message.content
                    if isinstance(block, TextBlock)
                )
            elif agent_event.type == "agent_end":
                terminal = agent_event
                succeeded = agent_event.status == "completed"
                error = agent_event.error or f"子 Agent 未完成：{agent_event.stop_reason}"

        async def forward_event(agent_event: AgentEvent) -> None:
            if subagent_request.on_event is not None:
                await subagent_request.on_event(
                    {
                        "agent_type": subagent_request.agent_type.value,
                        "event": agent_event_to_dict(agent_event),
                    }
                )

        unsubscribe = agent.subscribe(collect_text)
        # 转发观察者可能因背压被移除，不能连带丢掉内部终态收集。
        unsubscribe_progress = agent.subscribe(forward_event) if subagent_request.on_event else None
        try:
            await agent.run(
                subagent_request.task,
                model=subagent_request.model,
                # 8 是成本上限，不代表任务复杂度。
                max_turns=8,
                permission_mode=permission_mode,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"子 Agent 执行失败：{exc}"
            succeeded = False
        finally:
            # 终态写入不经观察者，存储失败必须传播；清理不受父任务取消影响。
            async def finish() -> None:
                try:
                    await agent.close()
                    outcome = TaskOutcome(
                        status=terminal.status if terminal is not None else "unknown",
                        stop_reason=(
                            terminal.stop_reason.value
                            if terminal is not None and terminal.stop_reason
                            else None
                        ),
                        error=terminal.error if terminal is not None else error,
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    try:
                        await self._session_manager.update(session_id, task_outcome=outcome)
                    except Exception as exc:
                        raise ToolResultStorageError(
                            "子任务终态保存失败，执行结果需要核验"
                        ) from exc
                finally:
                    unsubscribe()
                    if unsubscribe_progress is not None:
                        unsubscribe_progress()

            cleanup_task = asyncio.create_task(finish())
            cancelled = False
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup_task.result()
            if cancelled:
                raise asyncio.CancelledError
        return SubagentResult(session_id, content if succeeded else error, succeeded)
