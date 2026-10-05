from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import (
    AssistantContent,
    AssistantMessage,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEventType,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

from .context import ContextManager
from .events import (
    AgentEvent,
    agent_end_event,
    agent_start_event,
    approval_requested_event,
    message_end_event,
    message_start_event,
    message_update_event,
    tool_execution_end_event,
    tool_execution_start_event,
    turn_end_event,
    turn_start_event,
)
from .session import SessionManager, analyze_resume
from .state import (
    AgentOptions,
    AgentRun,
    QueuedInput,
    RunControl,
    RunId,
    RunStatus,
    SteerResult,
    StopReason,
    TurnId,
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
AgentEventListener = Callable[[AgentEvent], Awaitable[None] | None]
Unsubscribe = Callable[[], None]
_MAX_STEERING_MESSAGES = 32


class AgentBusyError(RuntimeError):
    """Agent 已有活动 Run，不能启动另一个 Run。"""


class AgentNotRunningError(RuntimeError):
    """当前 Agent 没有可接收运行命令的 Run。"""


class AgentResumeError(RuntimeError):
    """Session 尾部不满足恢复条件。"""


class Agent:
    """持有运行依赖，并通过 AgentEvent 描述完整 ReAct 生命周期。"""

    def __init__(
        self,
        session_id: str,
        workspace: Path,
        options: AgentOptions,
        llm: LlmClient,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
        sessions: SessionManager,
        context: ContextManager,
        on_close: CloseHandler | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("Agent session_id 不能为空")
        self._session_id = session_id
        self._workspace = workspace.resolve()
        self._options = options
        self._llm = llm
        self._tools = tools
        self._tool_executor = tool_executor
        self._sessions = sessions
        self._context = context
        self._on_close = on_close
        self._run_control: RunControl | None = None
        self._run_lock = asyncio.Lock()
        self._listeners: dict[int, AgentEventListener] = {}
        self._next_listener_id = 0
        self._closed = False
        # 内部订阅先于 UI/SSE 订阅，保证完整消息落盘后才对外确认结束。
        self.subscribe(sessions.record_agent_event)

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

        messages = await self._sessions.messages(self._session_id)
        plan = analyze_resume(messages)
        if not plan.resumable:
            raise AgentResumeError(plan.reason)
        control = await self._start_run(
            prompt=None,
            system_prompt=system_prompt,
            permission_mode=permission_mode,
            max_turns=max_turns,
            thinking=thinking,
            initial_tool_results=plan.missing_tool_results,
        )
        await self._wait_for_control(control)

    def subscribe(self, listener: AgentEventListener) -> Unsubscribe:
        """订阅生命周期事件，并返回幂等取消订阅函数。"""

        if self._closed:
            raise RuntimeError("Agent 已关闭")
        listener_id = self._next_listener_id
        self._next_listener_id += 1
        self._listeners[listener_id] = listener

        def unsubscribe() -> None:
            self._listeners.pop(listener_id, None)

        return unsubscribe

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
            control.accepting_input = False
            control.abort_requested = True
            control.steering.clear()
            task = control.owner_task
            if task is None:
                control.run.cancel()
                control.done.set()
                self._run_control = None
            elif not task.done():
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
        initial_tool_results: tuple[ToolResultMessage, ...] = (),
    ) -> RunControl:
        """原子登记活动 Run，并把执行任务交给 Agent 持有。"""

        options = replace(
            self._options,
            max_turns=self._options.max_turns if max_turns is None else max_turns,
            thinking=self._options.thinking if thinking is None else thinking,
        )
        async with self._run_lock:
            if self._closed:
                raise RuntimeError("Agent 已关闭")
            if self._run_control is not None:
                raise AgentBusyError("Agent 正在运行，请使用 steer() 或等待完成")
            run = AgentRun(
                session_id=self._session_id,
                run_id=RunId.new().value,
                max_turns=options.max_turns,
                max_total_tokens=options.max_total_tokens,
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

        for listener in tuple(self._listeners.values()):
            result = listener(event)
            if inspect.isawaitable(result):
                await result

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
        try:
            await self._emit(agent_start_event(run))
            async with asyncio.timeout(options.max_duration_seconds):
                for result in initial_tool_results:
                    await self._emit_complete_message(run, "resume-repair", result)
                pending_user: UserMessage | None = None
                context_query = _last_user_text(await self._sessions.messages(self._session_id))
                if prompt is not None:
                    pending_user = UserMessage(prompt)
                    await self._sessions.ensure(
                        self._session_id,
                        self._workspace,
                        _default_session_name(prompt),
                    )
                    context_query = prompt
                dynamic_context = await self._context.build_system_context(
                    context_query,
                    self._workspace,
                )
                if dynamic_context:
                    system_prompt = f"{system_prompt}\n\n{dynamic_context}"

                call_counts: dict[str, int] = {}
                while run.status is RunStatus.RUNNING:
                    turn_reason = await self._run_turn(
                        run=run,
                        system_prompt=system_prompt,
                        permission_mode=permission_mode,
                        options=options,
                        call_counts=call_counts,
                        user_message=pending_user,
                    )
                    pending_user = None
                    if run.status is not RunStatus.RUNNING:
                        continue
                    queued = await self._next_steering(control, turn_reason)
                    if queued is not None:
                        pending_user = queued.message
                    elif turn_reason is LlmStopReason.STOP:
                        run.complete()
            await self._emit(agent_end_event(run))
        except TimeoutError as exc:
            run.fail(StopReason.TIMEOUT)
            await self._repair_dangling_tool_calls(run)
            await self._emit(agent_end_event(run, str(exc) or "Agent 运行超时"))
        except asyncio.CancelledError:
            run.cancel()
            await asyncio.shield(self._repair_dangling_tool_calls(run))
            if control.abort_requested:
                await self._emit(agent_end_event(run, "Agent 运行已取消"))
            else:
                raise
        except Exception as exc:
            if run.stop_reason is None:
                run.fail(StopReason.PROVIDER_FAILED)
            await self._repair_dangling_tool_calls(run)
            await self._emit(agent_end_event(run, str(exc)))
        finally:
            control.done.set()
            async with self._run_lock:
                if self._run_control is control:
                    self._run_control = None

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

        messages = await self._sessions.messages(self._session_id)
        plan = analyze_resume(messages)
        for result in plan.missing_tool_results:
            await self._emit_complete_message(run, "interruption-repair", result)

    async def _emit_complete_message(self, run: AgentRun, turn_id: str, message: Message) -> None:
        """恢复修复也走消息事件，避免出现绕过会话订阅者的保存路径。"""

        message_id = uuid4().hex
        await self._emit(message_start_event(run, turn_id, message_id, message.role))
        await self._emit(message_end_event(run, turn_id, message_id, message))

    async def close(self) -> None:
        """幂等关闭 Agent 持有的外部连接。"""

        if self._closed:
            return
        self._closed = True
        await self.abort()
        await self.wait_for_idle()
        self._listeners.clear()
        if self._on_close is not None:
            await self._on_close()

    async def _run_turn(
        self,
        *,
        run: AgentRun,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        call_counts: dict[str, int],
        user_message: UserMessage | None,
    ) -> LlmStopReason:
        """执行一次 assistant 响应及其产生的全部工具调用。"""

        run.next_turn()
        turn_id = TurnId.new().value
        await self._emit(turn_start_event(run, turn_id))
        if user_message is not None:
            user_message_id = uuid4().hex
            await self._emit(message_start_event(run, turn_id, user_message_id, "user"))
            await self._emit(message_end_event(run, turn_id, user_message_id, user_message))

        history = await self._load_context(run, options)
        request = LlmRequest(
            model=options.model,
            messages=history,
            system_prompt=system_prompt,
            tools=self._tools.llm_definitions(),
            max_output_tokens=options.max_output_tokens,
            thinking=options.thinking,
        )
        assistant_message_id = uuid4().hex
        await self._emit(message_start_event(run, turn_id, assistant_message_id, "assistant"))
        assistant_message, calls = await self._collect_assistant_message(
            run,
            turn_id,
            assistant_message_id,
            request,
        )
        await self._emit(message_end_event(run, turn_id, assistant_message_id, assistant_message))

        tool_results: list[ToolResultMessage] = []
        if assistant_message.stop_reason is LlmStopReason.LENGTH:
            await self._reject_truncated_calls(run, turn_id, calls, tool_results)
        elif assistant_message.stop_reason is LlmStopReason.TOOL_USE:
            await self._execute_calls(
                run=run,
                turn_id=turn_id,
                calls=calls,
                call_counts=call_counts,
                permission_mode=permission_mode,
                tool_results=tool_results,
            )
        await self._emit(turn_end_event(run, turn_id, assistant_message, tuple(tool_results)))
        if assistant_message.stop_reason is LlmStopReason.LENGTH:
            run.fail(StopReason.MODEL_LENGTH)
        return assistant_message.stop_reason

    async def _collect_assistant_message(
        self,
        run: AgentRun,
        turn_id: str,
        message_id: str,
        request: LlmRequest,
    ) -> tuple[AssistantMessage, list[ToolCall]]:
        """实时发送模型增量，仅在完整结束后组装规范消息。"""

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: list[ToolCall] = []
        input_tokens = 0
        output_tokens = 0
        completion_reason: LlmStopReason | None = None
        async for llm_event in self._llm.stream(request):
            if llm_event.type is LlmStreamEventType.TEXT_DELTA:
                text_parts.append(str(llm_event.data.get("text", "")))
            elif llm_event.type is LlmStreamEventType.REASONING_DELTA:
                reasoning_parts.append(str(llm_event.data.get("text", "")))
            elif llm_event.type is LlmStreamEventType.TOOL_CALL_COMPLETED:
                calls.append(
                    ToolCall(
                        call_id=str(llm_event.data["call_id"]),
                        name=str(llm_event.data["name"]),
                        arguments=dict(llm_event.data.get("arguments", {})),
                    )
                )
            elif llm_event.type is LlmStreamEventType.USAGE_UPDATED:
                input_tokens = max(input_tokens, int(llm_event.data.get("input_tokens", 0)))
                output_tokens = max(output_tokens, int(llm_event.data.get("output_tokens", 0)))
            elif llm_event.type is LlmStreamEventType.MESSAGE_COMPLETED:
                try:
                    completion_reason = LlmStopReason(str(llm_event.data["stop_reason"]))
                except (KeyError, ValueError) as exc:
                    raise RuntimeError("MessageCompleted 缺少有效 stop_reason") from exc
            if llm_event.type not in {
                LlmStreamEventType.STREAM_STARTED,
                LlmStreamEventType.MESSAGE_COMPLETED,
            }:
                await self._emit(message_update_event(run, turn_id, message_id, llm_event))

        if completion_reason is None:
            raise RuntimeError("LLM 流缺少 MessageCompleted，禁止持久化不完整消息")
        if completion_reason is LlmStopReason.TOOL_USE and not calls:
            raise RuntimeError("LLM 以 tool_use 结束，但未返回完整工具调用")
        if completion_reason is LlmStopReason.STOP and calls:
            raise RuntimeError("LLM 以 stop 结束，但响应中存在工具调用")

        blocks: list[AssistantContent] = []
        if reasoning_parts:
            blocks.append(ReasoningBlock("".join(reasoning_parts)))
        if text_parts:
            blocks.append(TextBlock("".join(text_parts)))
        blocks.extend(ToolUseBlock(call.call_id, call.name, call.arguments) for call in calls)
        assistant_message = AssistantMessage(tuple(blocks), completion_reason)
        run.record_tokens(input_tokens, output_tokens)
        return assistant_message, calls

    async def _load_context(
        self,
        run: AgentRun,
        options: AgentOptions,
    ) -> tuple[Message, ...]:
        """在每个 Turn 前返回经压缩投影后的会话上下文。"""

        log = await self._sessions.context_log(run.session_id)
        compacted = await self._context.compact_if_needed(log, options)
        if compacted is not None:
            await self._sessions.replace_context(
                run.session_id,
                compacted.start_seq,
                compacted.end_seq,
                compacted.summary_message,
            )
            log.replace_context(
                compacted.start_seq,
                compacted.end_seq,
                compacted.summary_message,
            )
            run.record_tokens(compacted.input_tokens, compacted.output_tokens)
        return log.derive_messages()

    async def _reject_truncated_calls(
        self,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
        tool_results: list[ToolResultMessage],
    ) -> None:
        """闭合被长度截断的工具消息链，但不执行参数。"""

        for call in calls:
            await self._emit(tool_execution_start_event(run, turn_id, call))
            result = ToolResult(
                call.call_id,
                call.name,
                "模型输出达到长度上限，工具参数可能被截断，未执行该调用",
                is_error=True,
            )
            await self._record_tool_result(run, turn_id, result, tool_results)

    async def _execute_calls(
        self,
        *,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
        call_counts: dict[str, int],
        permission_mode: PermissionMode,
        tool_results: list[ToolResultMessage],
    ) -> None:
        """串行执行当前 Turn 的工具调用并实时投影审批事件。"""

        for call in calls:
            await self._emit(tool_execution_start_event(run, turn_id, call))
            signature = (
                f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
            )
            call_counts[signature] = call_counts.get(signature, 0) + 1
            if call_counts[signature] > 3:
                result = ToolResult(
                    call.call_id,
                    call.name,
                    "相同工具和参数已重复调用 3 次，执行被循环保护终止",
                    is_error=True,
                )
            else:
                approval_queue: asyncio.Queue[tuple[str, PermissionRequest, str]] = asyncio.Queue()

                async def on_approval(
                    request_id: str,
                    request: PermissionRequest,
                    reason: str,
                    queue: asyncio.Queue[tuple[str, PermissionRequest, str]] = approval_queue,
                ) -> None:
                    await queue.put((request_id, request, reason))

                execution = asyncio.create_task(
                    self._tool_executor.execute(
                        call,
                        ToolContext(run.session_id, self._workspace, permission_mode),
                        on_approval=on_approval,
                    )
                )
                try:
                    while not execution.done():
                        try:
                            pending = await asyncio.wait_for(approval_queue.get(), timeout=0.05)
                        except TimeoutError:
                            continue
                        await self._emit(approval_requested_event(run, turn_id, call, *pending))
                    while not approval_queue.empty():
                        await self._emit(
                            approval_requested_event(
                                run,
                                turn_id,
                                call,
                                *approval_queue.get_nowait(),
                            )
                        )
                    result = await execution
                except asyncio.CancelledError:
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise

            await self._record_tool_result(run, turn_id, result, tool_results)

    async def _record_tool_result(
        self,
        run: AgentRun,
        turn_id: str,
        result: ToolResult,
        tool_results: list[ToolResultMessage],
    ) -> None:
        """完整保存工具结果后发送消息事件，保持恢复所需的消息链。"""

        await self._emit(tool_execution_end_event(run, turn_id, result))
        message = _tool_result_message(result)
        tool_results.append(message)
        message_id = f"tool-{result.call_id}"
        await self._emit(message_start_event(run, turn_id, message_id, "toolResult"))
        await self._emit(message_end_event(run, turn_id, message_id, message))


def _default_session_name(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:60] or "新会话"


def _last_user_text(messages: tuple[Message, ...]) -> str:
    """为动态上下文检索返回最近一条用户文字。"""

    return next(
        (message.content for message in reversed(messages) if isinstance(message, UserMessage)),
        "继续当前任务",
    )


def _tool_result_message(result: ToolResult) -> ToolResultMessage:
    """将 Agent 工具结果投影为 LLM 规范消息。"""

    return ToolResultMessage(
        call_id=result.call_id,
        name=result.name,
        content=result.content,
        is_error=result.is_error,
    )
