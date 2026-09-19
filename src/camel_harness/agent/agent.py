from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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
from .session import SessionManager
from .state import AgentOptions, AgentRun, RunId, RunStatus, StopReason, TurnId
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


class Agent:
    """持有运行依赖，并通过 AgentEvent 描述完整 ReAct 生命周期。"""

    def __init__(
        self,
        options: AgentOptions,
        llm: LlmClient,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
        sessions: SessionManager,
        context: ContextManager,
        on_close: CloseHandler | None = None,
    ) -> None:
        self._options = options
        self._llm = llm
        self._tools = tools
        self._tool_executor = tool_executor
        self._sessions = sessions
        self._context = context
        self._on_close = on_close
        self._closed = False

    async def run(
        self,
        session_id: str,
        prompt: str,
        workspace: Path,
        *,
        system_prompt: str = "你是一个可靠的 Harness Agent。",
        max_turns: int | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        thinking: bool | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """运行一次 Agent，其中可包含多个 assistant/tool Turn。"""

        options = replace(
            self._options,
            max_turns=self._options.max_turns if max_turns is None else max_turns,
            thinking=self._options.thinking if thinking is None else thinking,
        )
        run = AgentRun(
            session_id=session_id,
            run_id=RunId.new().value,
            max_turns=options.max_turns,
            max_total_tokens=options.max_total_tokens,
        )
        run.start()
        yield agent_start_event(run)
        try:
            async with asyncio.timeout(options.max_duration_seconds):
                user_message = UserMessage(prompt)
                await self._sessions.ensure(session_id, workspace, _default_session_name(prompt))
                await self._sessions.append_message(session_id, user_message)
                dynamic_context = await self._context.build_system_context(prompt, workspace)
                if dynamic_context:
                    system_prompt = f"{system_prompt}\n\n{dynamic_context}"

                call_counts: dict[str, int] = {}
                pending_user: UserMessage | None = user_message
                while run.status is RunStatus.RUNNING:
                    async for event in self._run_turn(
                        run=run,
                        workspace=workspace,
                        system_prompt=system_prompt,
                        permission_mode=permission_mode,
                        options=options,
                        call_counts=call_counts,
                        user_message=pending_user,
                    ):
                        yield event
                    pending_user = None
            yield agent_end_event(run)
        except TimeoutError as exc:
            run.fail(StopReason.TIMEOUT)
            yield agent_end_event(run, str(exc) or "Agent 运行超时")
        except asyncio.CancelledError:
            run.cancel()
            yield agent_end_event(run, "Agent 运行已取消")
            raise
        except Exception as exc:
            if run.stop_reason is None:
                run.fail(StopReason.PROVIDER_FAILED)
            yield agent_end_event(run, str(exc))

    async def close(self) -> None:
        """幂等关闭 Agent 持有的外部连接。"""

        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            await self._on_close()

    async def _run_turn(
        self,
        *,
        run: AgentRun,
        workspace: Path,
        system_prompt: str,
        permission_mode: PermissionMode,
        options: AgentOptions,
        call_counts: dict[str, int],
        user_message: UserMessage | None,
    ) -> AsyncIterator[AgentEvent]:
        """执行一次 assistant 响应及其产生的全部工具调用。"""

        run.next_turn()
        turn_id = TurnId.new().value
        yield turn_start_event(run, turn_id)
        if user_message is not None:
            user_message_id = uuid4().hex
            yield message_start_event(run, turn_id, user_message_id, "user")
            yield message_end_event(run, turn_id, user_message_id, user_message)

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
        yield message_start_event(run, turn_id, assistant_message_id, "assistant")
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
                yield message_update_event(run, turn_id, assistant_message_id, llm_event)

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
        await self._sessions.append_message(run.session_id, assistant_message)
        yield message_end_event(run, turn_id, assistant_message_id, assistant_message)

        tool_results: list[ToolResultMessage] = []
        if completion_reason is LlmStopReason.LENGTH:
            async for event in self._reject_truncated_calls(
                run,
                turn_id,
                calls,
                tool_results,
            ):
                yield event
            yield turn_end_event(run, turn_id, assistant_message, tuple(tool_results))
            run.fail(StopReason.MODEL_LENGTH)
            return
        if completion_reason is LlmStopReason.STOP:
            yield turn_end_event(run, turn_id, assistant_message, ())
            run.complete()
            return

        async for event in self._execute_calls(
            run=run,
            turn_id=turn_id,
            calls=calls,
            call_counts=call_counts,
            workspace=workspace,
            permission_mode=permission_mode,
            tool_results=tool_results,
        ):
            yield event
        yield turn_end_event(run, turn_id, assistant_message, tuple(tool_results))

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
            run.record_tokens(compacted.input_tokens, compacted.output_tokens)
        return await self._sessions.context_messages(run.session_id)

    async def _reject_truncated_calls(
        self,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
        tool_results: list[ToolResultMessage],
    ) -> AsyncIterator[AgentEvent]:
        """闭合被长度截断的工具消息链，但不执行参数。"""

        for call in calls:
            yield tool_execution_start_event(run, turn_id, call)
            result = ToolResult(
                call.call_id,
                call.name,
                "模型输出达到长度上限，工具参数可能被截断，未执行该调用",
                is_error=True,
            )
            yield tool_execution_end_event(run, turn_id, result)
            message = _tool_result_message(result)
            await self._sessions.append_message(run.session_id, message)
            tool_results.append(message)
            message_id = f"tool-{call.call_id}"
            yield message_start_event(run, turn_id, message_id, "toolResult")
            yield message_end_event(run, turn_id, message_id, message)

    async def _execute_calls(
        self,
        *,
        run: AgentRun,
        turn_id: str,
        calls: list[ToolCall],
        call_counts: dict[str, int],
        workspace: Path,
        permission_mode: PermissionMode,
        tool_results: list[ToolResultMessage],
    ) -> AsyncIterator[AgentEvent]:
        """串行执行当前 Turn 的工具调用并实时投影审批事件。"""

        for call in calls:
            yield tool_execution_start_event(run, turn_id, call)
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
                        yield approval_requested_event(run, turn_id, call, *pending)
                    while not approval_queue.empty():
                        yield approval_requested_event(
                            run,
                            turn_id,
                            call,
                            *approval_queue.get_nowait(),
                        )
                    result = await execution
                except asyncio.CancelledError:
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise

            yield tool_execution_end_event(run, turn_id, result)
            message = _tool_result_message(result)
            await self._sessions.append_message(run.session_id, message)
            tool_results.append(message)
            message_id = f"tool-{call.call_id}"
            yield message_start_event(run, turn_id, message_id, "toolResult")
            yield message_end_event(run, turn_id, message_id, message)


def _default_session_name(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:60] or "新会话"


def _tool_result_message(result: ToolResult) -> ToolResultMessage:
    """将 Agent 工具结果投影为 LLM 规范消息。"""

    return ToolResultMessage(
        call_id=result.call_id,
        name=result.name,
        content=result.content,
        is_error=result.is_error,
    )
