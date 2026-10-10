from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import (
    AssistantMessage,
    AssistantMessageAccumulator,
    LlmClient,
    LlmErrorCode,
    LlmRequest,
    LlmStopReason,
    LlmStreamEventType,
    Message,
    StreamFailed,
    ToolDefinition,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

from .context import CompactionTrigger, ContextManager, ContextResourceError
from .events import (
    AgentEndEvent,
    AgentEndStatus,
    AgentEvent,
    AgentEventListener,
    AgentStartEvent,
    ApprovalRequestedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
    Unsubscribe,
    _EventDispatcher,
)
from .session import Session, analyze_resume
from .state import (
    AgentOptions,
    AgentRun,
    QueuedInput,
    RunControl,
    RunStatus,
    SteerResult,
    StopReason,
)
from .tools import (
    PermissionMode,
    PermissionRequest,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)

CloseHandler = Callable[[], Awaitable[None]]
_MAX_STEERING_MESSAGES = 32


class AgentBusyError(RuntimeError):
    """Agent 已有活动 Run，不能启动另一个 Run。"""


class AgentNotRunningError(RuntimeError):
    """当前 Agent 没有可接收运行命令的 Run。"""


class AgentResumeError(RuntimeError):
    """Session 尾部不满足恢复条件。"""


class _ContextOverflowError(RuntimeError):
    """首个流事件前拒绝的超限请求，可在修改上下文后重发。"""


class Agent:
    """持有运行依赖，并通过 AgentEvent 描述完整 ReAct 生命周期。"""

    def __init__(
        self,
        session: Session,
        workspace: Path,
        options: AgentOptions,
        llm: LlmClient,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
        context: ContextManager,
        on_close: CloseHandler | None = None,
    ) -> None:
        self._session_id = session.session_id
        self._workspace = workspace.resolve()
        self._options = options
        self._llm = llm
        self._tools = tools
        self._tool_executor = tool_executor
        self._session = session
        self._context = context
        self._on_close = on_close
        self._run_control: RunControl | None = None
        self._run_lock = asyncio.Lock()
        self._events = _EventDispatcher()
        self._closed = False
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> str:
        """返回此 Agent 唯一绑定的 Session。"""

        return self._session_id

    @property
    def workspace(self) -> Path:
        """返回此 Agent 唯一绑定的工作区。"""

        return self._workspace

    @property
    def is_running(self) -> bool:
        """说明当前是否存在尚未完成的 Run。"""

        return self._run_control is not None

    async def run(
        self,
        prompt: str,
        *,
        system_prompt: str = "你是一个可靠的 Harness Agent。",
        max_turns: int | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        thinking: bool | None = None,
    ) -> None:
        """运行一次 Agent，并在终态事件交付完成后返回。"""

        prompt = prompt.strip()
        if not prompt:
            raise ValueError("用户消息不能为空")
        control = await self._start_run(
            prompt=prompt,
            system_prompt=system_prompt,
            permission_mode=permission_mode,
            max_turns=max_turns,
            thinking=thinking,
        )
        await self._wait_for_control(control)

    async def resume(
        self,
        *,
        system_prompt: str = "你是一个可靠的 Harness Agent。",
        max_turns: int | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        thinking: bool | None = None,
    ) -> None:
        """从 Session 最后一个完整且可继续的消息边界创建新 Run。"""

        control = await self._start_run(
            prompt=None,
            system_prompt=system_prompt,
            permission_mode=permission_mode,
            max_turns=max_turns,
            thinking=thinking,
        )
        await self._wait_for_control(control)

    def subscribe(self, listener: AgentEventListener) -> Unsubscribe:
        """订阅生命周期事件，并返回幂等取消订阅函数。"""

        if self._closed or self._closing:
            raise RuntimeError("Agent 已关闭")
        return self._events.subscribe(listener)

    async def steer(self, content: str) -> SteerResult:
        """把用户输入加入当前 Run 的 FIFO，在下一个安全点消费。"""

        value = content.strip()
        if not value:
            raise ValueError("运行中输入不能为空")
        if len(value) > 100_000:
            raise ValueError("运行中输入不能超过 100000 个字符")
        async with self._run_lock:
            control = self._run_control
            if control is None or not control.accepting_input:
                raise AgentNotRunningError("当前 Agent 不接受运行中输入")
            if len(control.steering) >= _MAX_STEERING_MESSAGES:
                raise OverflowError("运行中输入队列已满")
            queued = QueuedInput(uuid4().hex, UserMessage(value))
            control.steering.append(queued)
            return SteerResult(queued.input_id, control.run.run_id)

    async def abort(self) -> bool:
        """幂等中断当前 Run，并清空尚未消费的 steering。"""

        async with self._run_lock:
            control = self._run_control
            if control is None:
                return False
            if control.abort_requested:
                return True
            if control.run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                return False
            control.accepting_input = False
            control.abort_requested = True
            control.steering.clear()
            task = control.owner_task
            if task is None:
                control.run.cancel()
                control.done.set()
                self._run_control = None
            elif not task.done() and control.run.status is RunStatus.RUNNING:
                task.cancel()
            return True

    async def wait_for_idle(self) -> None:
        """等待当前 Run 的终态事件和清理全部完成。"""

        control = self._run_control
        if control is not None:
            await control.done.wait()

    async def _start_run(
        self,
        *,
        prompt: str | None,
        system_prompt: str,
        permission_mode: PermissionMode,
        max_turns: int | None,
        thinking: bool | None,
    ) -> RunControl:
        """原子登记活动 Run，并把执行任务交给 Agent 持有。"""

        options = replace(
            self._options,
            max_turns=self._options.max_turns if max_turns is None else max_turns,
            thinking=self._options.thinking if thinking is None else thinking,
        )
        async with self._run_lock:
            if self._closed or self._closing:
                raise RuntimeError("Agent 已关闭")
            if self._run_control is not None:
                raise AgentBusyError("Agent 正在运行，请使用 steer() 或等待完成")
            initial_tool_results: tuple[ToolResultMessage, ...] = ()
            if prompt is None:
                # 空闲检查与恢复边界读取共享启动锁，避免使用并发 Run 的过期计划。
                plan = analyze_resume(await self._session.messages())
                if not plan.resumable:
                    raise AgentResumeError(plan.reason)
                initial_tool_results = plan.missing_tool_results
            run = AgentRun(
                session_id=self._session_id,
                run_id=uuid4().hex,
                max_turns=options.max_turns,
            )
            control = RunControl(run, asyncio.Event())
            self._run_control = control
            control.owner_task = asyncio.create_task(
                self._execute_run(
                    control,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    permission_mode=permission_mode,
                    options=options,
                    initial_tool_results=initial_tool_results,
                )
            )
            return control

    async def _wait_for_control(self, control: RunControl) -> None:
        """等待 Agent 自己持有的任务，调用方取消不会隐式接管 Run。"""

        task = control.owner_task
        if task is None:
            raise RuntimeError("Agent Run 未正确启动")
        await asyncio.shield(task)

    async def _emit(self, event: AgentEvent) -> None:
        """按订阅顺序交付事件，使慢消费者形成明确背压。"""

        await self._events.emit(event)

    async def _finish_message(
        self, run: AgentRun, turn_id: str, message_id: str, message: Message
    ) -> None:
        """先保存完整消息，再确认完成；通知失败不撤销已保存的消息。"""

        try:
            await self._session.append_message(message)
        except Exception:
            if run.status is RunStatus.RUNNING:
                run.fail(StopReason.SESSION_FAILED)
            raise
        await self._emit(
            MessageEndEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                message_id=message_id,
                message=message,
            )
        )

    async def _execute_run(
        self,
        control: RunControl,
        *,
        prompt: str | None,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        initial_tool_results: tuple[ToolResultMessage, ...] = (),
    ) -> None:
        """驱动一个已经登记的 Run，并确保唯一终态和状态清理。"""

        run = control.run
        run.start()
        error: str | None = None
        try:
            await self._emit(
                AgentStartEvent(
                    session_id=run.session_id, run_id=run.run_id, sequence=run.next_sequence()
                )
            )
            if control.abort_requested:
                raise asyncio.CancelledError
            async with asyncio.timeout(options.max_duration_seconds):
                for result in initial_tool_results:
                    await self._emit_complete_message(run, "resume-repair", result)
                pending_user: UserMessage | None = None
                if prompt is not None:
                    pending_user = UserMessage(prompt)
                    context_query = prompt
                else:
                    try:
                        context_query = self._context.resolve_query(await self._session.messages())
                    except Exception:
                        run.fail(StopReason.SESSION_FAILED)
                        raise
                length_recovery_used = False
                while run.status is RunStatus.RUNNING:
                    turn_reason, has_tool_calls = await self._run_turn(
                        run=run,
                        system_prompt=system_prompt,
                        permission_mode=permission_mode,
                        options=options,
                        context_query=context_query,
                        user_message=pending_user,
                    )
                    pending_user = None
                    if run.status is not RunStatus.RUNNING:
                        continue
                    if turn_reason is LlmStopReason.LENGTH:
                        # 只让模型重新规划一次，不执行或重试历史残缺参数。
                        if length_recovery_used or not has_tool_calls:
                            run.fail(StopReason.MODEL_LENGTH)
                            error = (
                                "模型输出达到长度上限；请提高 max_output_tokens 或拆分文件修改任务"
                            )
                            continue
                        length_recovery_used = True
                    queued = await self._next_steering(control, turn_reason)
                    if queued is not None:
                        pending_user = queued.message
                        context_query = queued.message.content
                    elif turn_reason is LlmStopReason.STOP:
                        run.complete()
        except TimeoutError as exc:
            if run.stop_reason is None:
                run.fail(StopReason.TIMEOUT)
            error = str(exc) or "Agent 运行超时"
        except asyncio.CancelledError:
            run.cancel()
            error = "Agent 运行已取消"
        except Exception as exc:
            if run.stop_reason is None:
                run.fail(StopReason.INTERNAL_ERROR)
            error = str(exc)
        finally:
            control.accepting_input = False
            try:
                if run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                    try:
                        await self._repair_dangling_tool_calls(run)
                    except Exception as exc:
                        error = f"{error or '运行未完成'}；工具链修复失败：{exc}"
                status: AgentEndStatus = "failed"
                if run.status is RunStatus.COMPLETED:
                    status = "completed"
                elif run.status is RunStatus.CANCELLED:
                    status = "cancelled"
                await self._emit(
                    AgentEndEvent(
                        session_id=run.session_id,
                        run_id=run.run_id,
                        sequence=run.next_sequence(),
                        status=status,
                        stop_reason=run.stop_reason,
                        total_tokens=run.total_tokens,
                        error=error,
                    )
                )
            finally:
                async with self._run_lock:
                    if self._run_control is control:
                        self._run_control = None
                control.done.set()

    async def _next_steering(
        self,
        control: RunControl,
        turn_reason: LlmStopReason | None,
    ) -> QueuedInput | None:
        """在 Turn 安全点消费一条输入，或原子关闭 Run 的输入窗口。"""

        async with self._run_lock:
            if control.steering:
                return control.steering.popleft()
            if turn_reason is LlmStopReason.STOP:
                control.accepting_input = False
            return None

    async def _repair_dangling_tool_calls(self, run: AgentRun) -> None:
        """为中断时已落盘但没有结果的工具调用追加错误结果。"""

        messages = await self._session.messages()
        plan = analyze_resume(messages)
        for result in plan.missing_tool_results:
            await self._emit_complete_message(run, "interruption-repair", result)

    async def _emit_complete_message(
        self, run: AgentRun, turn_id: str, message: Message, *, message_id: str | None = None
    ) -> None:
        """非流式完整消息共享开始、保存和结束路径。"""

        message_id = message_id or uuid4().hex
        await self._emit(
            MessageStartEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                message_id=message_id,
                role=message.role,
            )
        )
        await self._finish_message(run, turn_id, message_id, message)

    async def close(self) -> None:
        """幂等关闭 Agent 持有的外部连接。"""

        if self._closed:
            return
        self._closing = True
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_resources())
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        """关闭等待者取消不会中断清理，失败后允许再次尝试。"""
        await self.abort()
        await self.wait_for_idle()
        self._events.clear()
        if self._on_close is not None:
            await self._on_close()
        self._closed = True

    async def _run_turn(
        self,
        *,
        run: AgentRun,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        context_query: str,
        user_message: UserMessage | None,
    ) -> tuple[LlmStopReason, bool]:
        """执行一次 assistant 响应及其产生的全部工具调用。"""

        run.next_turn()
        turn_id = uuid4().hex
        await self._emit(
            TurnStartEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                turn=run.turn,
            )
        )
        if user_message is not None:
            await self._emit_complete_message(run, turn_id, user_message)
        definitions = self._tools.llm_definitions()
        base_system_prompt = system_prompt
        system_prompt, history = await self._prepare_context(
            run, options.model, system_prompt, context_query, definitions
        )
        request = LlmRequest(
            model=options.model,
            messages=history,
            system_prompt=system_prompt,
            tools=definitions,
            max_output_tokens=options.max_output_tokens,
            thinking=options.thinking,
        )
        assistant_message_id = uuid4().hex
        await self._emit(
            MessageStartEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                message_id=assistant_message_id,
                role="assistant",
            )
        )
        while True:
            try:
                assistant_message, calls = await self._collect_assistant_message(
                    run,
                    turn_id,
                    assistant_message_id,
                    request,
                )
                break
            except _ContextOverflowError as exc:
                if run.context_overflow_recovery_used:
                    run.fail(StopReason.CONTEXT_FAILED)
                    raise RuntimeError(
                        "上下文溢出恢复次数已用尽（每 Run 最多一次）；"
                        f"请减少输入或调整 compaction 配置：{exc}"
                    ) from exc
                # 同一 Turn 只重发尚未生成内容的模型请求，不重存输入或重跑工具。
                run.context_overflow_recovery_used = True
                system_prompt, history = await self._prepare_context(
                    run,
                    options.model,
                    base_system_prompt,
                    context_query,
                    definitions,
                    trigger=CompactionTrigger.CONTEXT_OVERFLOW,
                )
                request = replace(request, messages=history, system_prompt=system_prompt)
            except Exception:
                run.fail(StopReason.PROVIDER_FAILED)
                raise
        await self._finish_message(run, turn_id, assistant_message_id, assistant_message)

        tool_results: tuple[ToolResultMessage, ...] = ()
        if assistant_message.stop_reason is LlmStopReason.LENGTH:
            tool_results = await self._reject_truncated_calls(run, turn_id, calls)
        elif assistant_message.stop_reason is LlmStopReason.TOOL_USE:
            tool_results = await self._execute_calls(
                run=run,
                turn_id=turn_id,
                calls=calls,
                permission_mode=permission_mode,
            )
        await self._emit(
            TurnEndEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                message=assistant_message,
                tool_results=tool_results,
            )
        )
        return assistant_message.stop_reason, bool(calls)

    async def _collect_assistant_message(
        self,
        run: AgentRun,
        turn_id: str,
        message_id: str,
        request: LlmRequest,
    ) -> tuple[AssistantMessage, list[ToolCall]]:
        """实时发送模型增量，仅在完整结束后组装规范消息。"""

        accumulator = AssistantMessageAccumulator(self._llm.provider_id, request.model)
        started = False
        try:
            async for llm_event in self._llm.stream(request):
                accumulator.feed(llm_event)
                if llm_event.type not in {
                    LlmStreamEventType.STREAM_STARTED,
                    LlmStreamEventType.MESSAGE_COMPLETED,
                }:
                    await self._emit(
                        MessageUpdateEvent(
                            session_id=run.session_id,
                            run_id=run.run_id,
                            sequence=run.next_sequence(),
                            turn_id=turn_id,
                            message_id=message_id,
                            assistant_event=llm_event,
                        )
                    )
                if isinstance(llm_event, StreamFailed):
                    if llm_event.code is LlmErrorCode.CONTEXT_OVERFLOW and not started:
                        raise _ContextOverflowError(llm_event.error)
                    raise RuntimeError(llm_event.error)
                started = True
        finally:
            # 失败或取消也保留已知消耗，但绝不保存未完成 assistant。
            if accumulator.usage is not None:
                run.record_tokens(
                    accumulator.usage.input_tokens or 0, accumulator.usage.output_tokens or 0
                )

        assistant_message = accumulator.finish()
        calls = [
            ToolCall(block.call_id, block.name, block.arguments)
            for block in assistant_message.content
            if isinstance(block, ToolUseBlock)
        ]
        return assistant_message, calls

    async def _prepare_context(
        self,
        run: AgentRun,
        model: str,
        system_prompt: str,
        query: str,
        tools: tuple[ToolDefinition, ...],
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> tuple[str, tuple[Message, ...]]:
        """协调只读上下文准备与会话提交，不实现资源组装或压缩策略。"""

        try:
            snapshot = await self._session.context_snapshot()
        except Exception:
            run.fail(StopReason.SESSION_FAILED)
            raise
        try:
            prepared = await self._context.prepare(
                snapshot,
                model,
                query=query,
                workspace=self._workspace,
                on_usage=lambda usage: run.record_tokens(
                    usage.input_tokens or 0, usage.output_tokens or 0
                ),
                system_prompt=system_prompt,
                tools=tools,
                trigger=trigger,
            )
        except ContextResourceError:
            run.fail(StopReason.RESOURCE_FAILED)
            raise
        except Exception:
            run.fail(StopReason.CONTEXT_FAILED)
            raise
        compacted = prepared.compaction
        if compacted is None and trigger is CompactionTrigger.CONTEXT_OVERFLOW:
            run.fail(StopReason.CONTEXT_FAILED)
            raise RuntimeError("上下文溢出恢复未生成安全压缩计划，未重发请求")
        if compacted is not None:
            try:
                snapshot = await self._session.compact(
                    compacted.summary,
                    compacted.first_kept_entry_id,
                    snapshot=snapshot,
                    tokens_before=compacted.tokens_before,
                    usage=compacted.usage,
                )
            except Exception:
                run.fail(StopReason.SESSION_FAILED)
                raise
        return prepared.system_prompt, snapshot.messages

    async def _reject_truncated_calls(
        self,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
    ) -> tuple[ToolResultMessage, ...]:
        """闭合被长度截断的工具消息链，但不执行参数。"""

        tool_results: list[ToolResultMessage] = []
        for call in calls:
            await self._emit(
                ToolExecutionStartEvent(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    sequence=run.next_sequence(),
                    turn_id=turn_id,
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                )
            )
            result = ToolResult(
                call.call_id,
                call.name,
                "模型输出达到长度上限，工具参数可能被截断，未执行该调用。"
                "请重新生成完整且更小的参数，不要重复输出同一份超长内容。",
                is_error=True,
            )
            tool_results.append(await self._record_tool_result(run, turn_id, result))

        return tuple(tool_results)

    async def _execute_calls(
        self,
        *,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
        permission_mode: PermissionMode,
    ) -> tuple[ToolResultMessage, ...]:
        """串行执行当前 Turn 的工具调用并实时投影审批事件。"""

        tool_results: list[ToolResultMessage] = []
        for call in calls:
            await self._emit(
                ToolExecutionStartEvent(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    sequence=run.next_sequence(),
                    turn_id=turn_id,
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                )
            )

            async def on_approval(
                request_id: str,
                request: PermissionRequest,
                reason: str,
                current_call: ToolCall = call,
            ) -> None:
                await self._emit(
                    ApprovalRequestedEvent(
                        session_id=run.session_id,
                        run_id=run.run_id,
                        sequence=run.next_sequence(),
                        turn_id=turn_id,
                        request_id=request_id,
                        call_id=current_call.call_id,
                        name=current_call.name,
                        capabilities=tuple(sorted(request.capabilities)),
                        risk=request.risk,
                        arguments=request.arguments,
                        reason=reason,
                    )
                )

            try:
                result = await self._tool_executor.execute(
                    call,
                    ToolContext(run.session_id, self._workspace, permission_mode),
                    on_approval=on_approval,
                )
            except Exception:
                run.fail(StopReason.TOOL_FAILED)
                raise
            tool_results.append(await self._record_tool_result(run, turn_id, result))

        return tuple(tool_results)

    async def _record_tool_result(
        self,
        run: AgentRun,
        turn_id: str,
        result: ToolResult,
    ) -> ToolResultMessage:
        """完整保存工具结果后返回，保持恢复所需的消息链。"""

        await self._emit(
            ToolExecutionEndEvent(
                session_id=run.session_id,
                run_id=run.run_id,
                sequence=run.next_sequence(),
                turn_id=turn_id,
                result=result,
            )
        )
        message = result.to_message()
        await self._emit_complete_message(
            run, turn_id, message, message_id=f"tool-{result.call_id}"
        )
        return message
