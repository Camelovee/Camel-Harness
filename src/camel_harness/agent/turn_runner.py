"""一次 Turn 的上下文准备、模型流、工具分发与结束事件。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import (
    AssistantMessage,
    AssistantMessageAccumulator,
    LlmClient,
    LlmErrorCode,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEventType,
    LlmThinkingEffort,
    Message,
    StreamFailed,
    ToolDefinition,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

from .context import CompactionTrigger, ContextManager, ContextResourceError, ContextSessionError
from .events import (
    AgentEvent,
    ApprovalRequestedEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .session import Session
from .session_committer import SessionCommitter
from .state import AgentRun, StopReason
from .tools import (
    PermissionMode,
    PermissionRequest,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolResult,
)

EmitEvent = Callable[[AgentEvent], Awaitable[None]]


class _ContextOverflowError(RuntimeError):
    """首个流事件前拒绝的超限请求，可在修改上下文后重发。"""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """一次 Turn 结束后的模型停止原因与工具调用情况。"""

    stop_reason: LlmStopReason
    has_tool_calls: bool


class TurnRunner:
    """执行单次 Turn，不负责 Run 级终态与取消。"""

    def __init__(
        self,
        *,
        session: Session,
        workspace: Path,
        llm_client: LlmClient,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        committer: SessionCommitter,
        emit: EmitEvent,
        agent_run: AgentRun,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._llm_client = llm_client
        self._tool_executor = tool_executor
        self._context_manager = context_manager
        self._committer = committer
        self._emit = emit
        self._agent_run = agent_run

    async def run_turn(
        self,
        *,
        model: LlmModel,
        permission_mode: PermissionMode,
        effort: LlmThinkingEffort,
        user_message: UserMessage | None,
    ) -> TurnOutcome:
        """执行一次 assistant 响应及其产生的全部工具调用。"""

        agent_run = self._agent_run
        # next_turn() 可能先 fail(MAX_TURNS) 再抛错；超限的 Turn 不会发出 TurnStart。
        agent_run.next_turn()
        turn_id = uuid4().hex
        await self._emit(
            TurnStartEvent(
                session_id=agent_run.session_id,
                run_id=agent_run.run_id,
                sequence=agent_run.next_sequence(),
                turn_id=turn_id,
                turn=agent_run.turn,
            )
        )
        if user_message is not None:
            await self._committer.emit_complete_message(turn_id, user_message)
        tool_definitions = self._tool_executor.llm_definitions()
        system_prompt, history = await self._prepare_context(model, tool_definitions)
        llm_request = LlmRequest(
            model=model,
            messages=history,
            system_prompt=system_prompt,
            tools=tool_definitions,
            max_output_tokens=model.max_output_tokens,
            effort=effort,
        )
        assistant_message_id = uuid4().hex
        # MessageStart 在流开始前发出；失败或取消时不会配对 MessageEnd，也不持久化，
        # 事件消费者必须容忍孤立的 start。
        await self._committer.emit_message_start(turn_id, assistant_message_id, "assistant")
        while True:
            try:
                assistant_message, tool_calls = await self._collect_assistant_message(
                    turn_id, assistant_message_id, llm_request
                )
                break
            except _ContextOverflowError as exc:
                if agent_run.context_overflow_recovery_used:
                    agent_run.fail(StopReason.CONTEXT_FAILED)
                    raise RuntimeError(
                        "上下文溢出恢复次数已用尽（每 Run 最多一次）；"
                        f"请减少输入或调整 compaction 配置：{exc}"
                    ) from exc
                # 同一 Turn 只重发尚未生成内容的模型请求，不重存输入或重跑工具。
                # 恢复标记是 Run 级且永不重置，每个 Run 最多恢复一次。
                agent_run.context_overflow_recovery_used = True
                system_prompt, history = await self._prepare_context(
                    model,
                    tool_definitions,
                    trigger=CompactionTrigger.CONTEXT_OVERFLOW,
                )
                llm_request = replace(
                    llm_request,
                    messages=history,
                    system_prompt=system_prompt,
                )
            except Exception:
                agent_run.fail(StopReason.PROVIDER_FAILED)
                raise
        agent_run.last_output_budget = llm_request.max_output_tokens
        usage = assistant_message.usage
        agent_run.last_output_tokens = usage.output_tokens if usage else None
        agent_run.last_reasoning_tokens = usage.reasoning_tokens if usage else None
        await self._committer.finish_message(turn_id, assistant_message_id, assistant_message)

        tool_results: tuple[ToolResultMessage, ...] = ()
        if assistant_message.stop_reason is LlmStopReason.LENGTH:
            tool_results = await self._reject_truncated_calls(turn_id, tool_calls)
        elif assistant_message.stop_reason is LlmStopReason.TOOL_USE:
            tool_results = await self._execute_calls(
                turn_id=turn_id,
                tool_calls=tool_calls,
                permission_mode=permission_mode,
                model=model,
            )
        await self._emit(
            TurnEndEvent(
                session_id=agent_run.session_id,
                run_id=agent_run.run_id,
                sequence=agent_run.next_sequence(),
                turn_id=turn_id,
                message=assistant_message,
                tool_results=tool_results,
            )
        )
        return TurnOutcome(assistant_message.stop_reason, bool(tool_calls))

    async def _collect_assistant_message(
        self,
        turn_id: str,
        message_id: str,
        llm_request: LlmRequest,
    ) -> tuple[AssistantMessage, list[ToolCall]]:
        """实时发送模型增量，仅在完整结束后组装规范消息。"""

        agent_run = self._agent_run
        accumulator = AssistantMessageAccumulator(
            llm_request.model.provider, llm_request.model.model
        )
        started = False
        try:
            async for llm_event in self._llm_client.stream(llm_request):
                accumulator.feed(llm_event)
                if llm_event.type not in {
                    LlmStreamEventType.STREAM_STARTED,
                    LlmStreamEventType.MESSAGE_COMPLETED,
                }:
                    await self._emit(
                        MessageUpdateEvent(
                            session_id=agent_run.session_id,
                            run_id=agent_run.run_id,
                            sequence=agent_run.next_sequence(),
                            turn_id=turn_id,
                            message_id=message_id,
                            assistant_event=llm_event,
                        )
                    )
                if isinstance(llm_event, StreamFailed):
                    if llm_event.code is LlmErrorCode.CONTEXT_OVERFLOW and not started:
                        raise _ContextOverflowError(llm_event.error)
                    raise RuntimeError(llm_event.error)
                # 一旦收到任意流事件，overflow 不再可恢复，否则会重复投递已发出的增量。
                started = True
        finally:
            # 失败或取消也保留已知消耗，但绝不保存未完成 assistant。
            if accumulator.usage is not None:
                agent_run.record_tokens(
                    accumulator.usage.input_tokens or 0, accumulator.usage.output_tokens or 0
                )

        assistant_message = accumulator.finish()
        tool_calls = [
            ToolCall(block.call_id, block.name, block.arguments)
            for block in assistant_message.content
            if isinstance(block, ToolUseBlock)
        ]
        return assistant_message, tool_calls

    async def _prepare_context(
        self,
        model: LlmModel,
        tool_definitions: tuple[ToolDefinition, ...],
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> tuple[str, tuple[Message, ...]]:
        """为上下文准备接入运行用量与故障分类，不编排会话提交。"""
        agent_run = self._agent_run
        try:
            prepared = await self._context_manager.prepare(
                self._session,
                model,
                workspace=self._workspace,
                on_usage=lambda usage: agent_run.record_tokens(
                    usage.input_tokens or 0, usage.output_tokens or 0
                ),
                tool_definitions=tool_definitions,
                trigger=trigger,
            )
        except ContextSessionError:
            # 分类按责任方划分且顺序不可调换：会话 → SESSION，资源 → RESOURCE，
            # 其余上下文/压缩/提示词故障 → CONTEXT。
            agent_run.fail(StopReason.SESSION_FAILED)
            raise
        except ContextResourceError:
            agent_run.fail(StopReason.RESOURCE_FAILED)
            raise
        except Exception:
            agent_run.fail(StopReason.CONTEXT_FAILED)
            raise
        return prepared.system_prompt, prepared.messages

    async def _reject_truncated_calls(
        self,
        turn_id: str,
        tool_calls: list[ToolCall],
    ) -> tuple[ToolResultMessage, ...]:
        """闭合被长度截断的工具消息链，但不执行参数。"""

        tool_results: list[ToolResultMessage] = []
        for tool_call in tool_calls:
            await self._emit_tool_start(turn_id, tool_call)
            tool_result = ToolResult(
                tool_call.call_id,
                tool_call.name,
                "模型输出达到长度上限，工具参数可能被截断，未执行该调用。"
                "请重新生成完整且更小的参数，不要重复输出同一份超长内容。",
                is_error=True,
                metadata={"execution_status": "not_executed"},
            )
            tool_results.append(await self._record_tool_result(turn_id, tool_result))

        return tuple(tool_results)

    async def _execute_calls(
        self,
        *,
        turn_id: str,
        tool_calls: list[ToolCall],
        permission_mode: PermissionMode,
        model: LlmModel,
    ) -> tuple[ToolResultMessage, ...]:
        """串行执行当前 Turn 的工具调用并实时投影审批事件。"""

        agent_run = self._agent_run
        tool_results: list[ToolResultMessage] = []
        for tool_call in tool_calls:
            await self._emit_tool_start(turn_id, tool_call)

            async def on_approval(
                request_id: str,
                permission_request: PermissionRequest,
                reason: str,
                # 默认参数在当前迭代捕获 tool_call，规避闭包晚绑定。
                current_tool_call: ToolCall = tool_call,
            ) -> None:
                await self._emit(
                    ApprovalRequestedEvent(
                        session_id=agent_run.session_id,
                        run_id=agent_run.run_id,
                        sequence=agent_run.next_sequence(),
                        turn_id=turn_id,
                        request_id=request_id,
                        call_id=current_tool_call.call_id,
                        name=current_tool_call.name,
                        capabilities=tuple(sorted(permission_request.capabilities)),
                        risk=permission_request.risk,
                        arguments=permission_request.arguments,
                        reason=reason,
                        execution_context=dict(permission_request.execution_context),
                    )
                )

            try:
                tool_result = await self._tool_executor.execute(
                    tool_call,
                    ToolContext(
                        agent_run.session_id, self._workspace, permission_mode, model=model
                    ),
                    on_approval=on_approval,
                )
            except Exception:
                # ToolExecutor 已把工具失败归一化为 ToolResult；这里捕获的是基础设施或
                # 结果归档故障，因此升级为 TOOL_FAILED。
                agent_run.fail(StopReason.TOOL_FAILED)
                raise
            tool_results.append(await self._record_tool_result(turn_id, tool_result))

        return tuple(tool_results)

    async def _emit_tool_start(self, turn_id: str, tool_call: ToolCall) -> None:
        """在审批、拒绝或执行之前发送工具开始事件。"""

        await self._emit(
            ToolExecutionStartEvent(
                session_id=self._agent_run.session_id,
                run_id=self._agent_run.run_id,
                sequence=self._agent_run.next_sequence(),
                turn_id=turn_id,
                call_id=tool_call.call_id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )

    async def _record_tool_result(
        self,
        turn_id: str,
        tool_result: ToolResult,
    ) -> ToolResultMessage:
        """完整保存工具结果后返回，保持恢复所需的消息链。"""

        # 该事件描述“执行结果”，不是持久化确认，因此先于落盘发出；
        # 与 SessionCommitter 的“先保存后事件”相反，是有意为之。
        await self._emit(
            ToolExecutionEndEvent(
                session_id=self._agent_run.session_id,
                run_id=self._agent_run.run_id,
                sequence=self._agent_run.next_sequence(),
                turn_id=turn_id,
                result=tool_result,
            )
        )
        message = tool_result.to_message()
        await self._committer.emit_complete_message(
            turn_id, message, message_id=f"tool-{tool_result.call_id}"
        )
        return message
