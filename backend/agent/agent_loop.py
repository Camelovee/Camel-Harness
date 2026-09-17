from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from backend.llm import LlmClient, LlmRequest, LlmStreamEventType
from backend.tools import (
    PermissionRequest,
    ToolExecutor,
    ToolRegistry,
)
from backend.types import (
    ContentBlock,
    Message,
    MessageRole,
    PermissionMode,
    ReasoningBlock,
    StepId,
    TextBlock,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolResultBlock,
    ToolUseBlock,
    TurnId,
)

from .events import AgentStreamEvent, AgentStreamEventType
from .options import AgentOptions
from .run import AgentRun, RunStatus, StopReason

ContextLoader = Callable[[AgentRun, AgentOptions], Awaitable[tuple[Message, ...]]]
MessageSink = Callable[[Message], Awaitable[None]]


class AgentLoop:
    """低层 ReAct 循环，只协调模型、工具和流事件。"""

    def __init__(
        self,
        llm: LlmClient,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._tool_executor = tool_executor

    async def run(
        self,
        session_id: str,
        workspace: Path,
        system_prompt: str,
        options: AgentOptions,
        load_context: ContextLoader,
        on_message: MessageSink,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> AsyncIterator[AgentStreamEvent]:
        """启动一个 Turn，并通过回调读取上下文、提交完整消息。"""

        run = AgentRun(
            session_id=session_id,
            turn_id=TurnId.new().value,
            max_steps=options.max_steps,
            max_total_tokens=options.max_total_tokens,
        )
        run.start()
        last_event: AgentStreamEvent | None = None
        try:
            async with asyncio.timeout(options.max_duration_seconds):
                async for event in self._turn(
                    run,
                    workspace,
                    system_prompt,
                    permission_mode,
                    options,
                    load_context,
                    on_message,
                ):
                    last_event = event
                    yield event
            if last_event is not None:
                yield self._terminal_event(run, last_event, AgentStreamEventType.TURN_COMPLETED)
        except TimeoutError:
            run.fail(StopReason.TIMEOUT)
            if last_event is not None:
                yield self._terminal_event(run, last_event, AgentStreamEventType.TURN_FAILED)
            raise
        except asyncio.CancelledError:
            run.cancel()
            if last_event is not None:
                yield self._terminal_event(run, last_event, AgentStreamEventType.TURN_CANCELLED)
            raise
        except Exception:
            if run.stop_reason is None:
                run.fail(StopReason.PROVIDER_FAILED)
            if last_event is not None:
                yield self._terminal_event(run, last_event, AgentStreamEventType.TURN_FAILED)
            raise

    async def _turn(
        self,
        run: AgentRun,
        workspace: Path,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        load_context: ContextLoader,
        on_message: MessageSink,
    ) -> AsyncIterator[AgentStreamEvent]:
        """驱动 Step，直到模型完成或预算终止。"""

        call_counts: dict[str, int] = {}
        while run.status is RunStatus.RUNNING:
            async for event in self._step(
                run,
                workspace,
                system_prompt,
                permission_mode,
                options,
                load_context,
                on_message,
                call_counts,
            ):
                yield event

    async def _step(
        self,
        run: AgentRun,
        workspace: Path,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        load_context: ContextLoader,
        on_message: MessageSink,
        call_counts: dict[str, int],
    ) -> AsyncIterator[AgentStreamEvent]:
        """执行一次模型调用及其产生的全部工具调用。"""

        run.next_step()
        step_id = StepId.new().value
        history = await load_context(run, options)
        request = LlmRequest(
            session_id=run.session_id,
            turn_id=run.turn_id,
            step_id=step_id,
            provider=options.provider,
            model=options.model,
            messages=(Message.text(MessageRole.SYSTEM, system_prompt), *history),
            tools=self._tools.llm_definitions(),
            max_output_tokens=options.max_output_tokens,
            thinking=options.thinking,
        )
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: list[ToolCall] = []
        last_sequence = 0
        input_tokens = 0
        output_tokens = 0
        completed = False
        async for event in self._llm.stream(request):
            last_sequence = event.sequence
            if event.type is LlmStreamEventType.TEXT_DELTA:
                text_parts.append(str(event.data.get("text", "")))
            elif event.type is LlmStreamEventType.REASONING_DELTA:
                reasoning_parts.append(str(event.data.get("text", "")))
            elif event.type is LlmStreamEventType.TOOL_CALL_COMPLETED:
                calls.append(
                    ToolCall(
                        call_id=str(event.data["call_id"]),
                        name=str(event.data["name"]),
                        arguments=dict(event.data.get("arguments", {})),
                    )
                )
            elif event.type is LlmStreamEventType.USAGE_UPDATED:
                input_tokens = max(input_tokens, int(event.data.get("input_tokens", 0)))
                output_tokens = max(output_tokens, int(event.data.get("output_tokens", 0)))
            elif event.type is LlmStreamEventType.MESSAGE_COMPLETED:
                completed = True
            yield AgentStreamEvent.from_llm(event)

        if not completed:
            raise RuntimeError("LLM 流缺少 MessageCompleted，禁止持久化不完整消息")
        blocks: list[ContentBlock] = []
        if reasoning_parts:
            blocks.append(ReasoningBlock("".join(reasoning_parts)))
        if text_parts:
            blocks.append(TextBlock("".join(text_parts)))
        blocks.extend(ToolUseBlock(call.call_id, call.name, call.arguments) for call in calls)
        run.record_tokens(input_tokens, output_tokens)
        await on_message(Message(MessageRole.ASSISTANT, tuple(blocks)))
        if not calls:
            run.complete()
            yield self._step_completed(run, step_id, last_sequence + 1)
            return

        sequence = last_sequence
        for call in calls:
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
                        ToolContext(run.session_id, workspace.resolve(), permission_mode),
                        on_approval=on_approval,
                    )
                )
                try:
                    while not execution.done():
                        try:
                            pending = await asyncio.wait_for(approval_queue.get(), timeout=0.05)
                        except TimeoutError:
                            continue
                        sequence += 1
                        yield self._approval_event(run, step_id, sequence, call, *pending)
                    while not approval_queue.empty():
                        sequence += 1
                        yield self._approval_event(
                            run,
                            step_id,
                            sequence,
                            call,
                            *approval_queue.get_nowait(),
                        )
                    result = await execution
                except asyncio.CancelledError:
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise

            await on_message(
                Message(
                    MessageRole.TOOL,
                    (
                        ToolResultBlock(
                            call_id=result.call_id,
                            name=result.name,
                            content=result.content,
                            is_error=result.is_error,
                        ),
                    ),
                )
            )
            sequence += 1
            yield AgentStreamEvent(
                type=AgentStreamEventType.TOOL_RESULT,
                session_id=run.session_id,
                turn_id=run.turn_id,
                step_id=step_id,
                sequence=sequence,
                source="harness",
                data={
                    "call_id": result.call_id,
                    "name": result.name,
                    "content": result.content,
                    "is_error": result.is_error,
                },
            )
        yield self._step_completed(run, step_id, sequence + 1)

    @staticmethod
    def _step_completed(run: AgentRun, step_id: str, sequence: int) -> AgentStreamEvent:
        return AgentStreamEvent(
            type=AgentStreamEventType.STEP_COMPLETED,
            session_id=run.session_id,
            turn_id=run.turn_id,
            step_id=step_id,
            sequence=sequence,
            source="harness",
            data={"step": run.step},
        )

    @staticmethod
    def _approval_event(
        run: AgentRun,
        step_id: str,
        sequence: int,
        call: ToolCall,
        request_id: str,
        request: PermissionRequest,
        reason: str,
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            type=AgentStreamEventType.APPROVAL_REQUESTED,
            session_id=run.session_id,
            turn_id=run.turn_id,
            step_id=step_id,
            sequence=sequence,
            source="harness",
            data={
                "request_id": request_id,
                "call_id": call.call_id,
                "name": call.name,
                "capabilities": sorted(request.capabilities),
                "risk": request.risk,
                "arguments": request.arguments,
                "reason": reason,
            },
        )

    @staticmethod
    def _terminal_event(
        run: AgentRun,
        previous: AgentStreamEvent,
        event_type: AgentStreamEventType,
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            type=event_type,
            session_id=run.session_id,
            turn_id=run.turn_id,
            step_id=previous.step_id,
            sequence=previous.sequence + 1,
            source="harness",
            data={"stop_reason": run.stop_reason.value if run.stop_reason else None},
        )
