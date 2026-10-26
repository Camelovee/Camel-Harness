from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import (
    LlmClient,
    LlmModel,
    LlmStopReason,
    LlmThinkingEffort,
    ToolResultMessage,
    UserMessage,
)

from .context import ContextManager
from .events import (
    AgentEndEvent,
    AgentEndStatus,
    AgentEvent,
    AgentEventListener,
    AgentStartEvent,
    Unsubscribe,
    _EventDispatcher,
)
from .session import Session, analyze_resume
from .session_committer import SessionCommitter
from .state import (
    AgentOptions,
    AgentRun,
    QueuedInput,
    RunControl,
    RunStatus,
    SteerResult,
    StopReason,
)
from .tools import PermissionMode, ToolExecutor
from .turn_runner import TurnRunner

CloseHandler = Callable[[], Awaitable[None]]


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
        session: Session,
        workspace: Path,
        agent_options: AgentOptions,
        llm_client: LlmClient,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        *,
        on_close: CloseHandler | None = None,
    ) -> None:
        self._session_id = session.session_id
        self._workspace = workspace.resolve()
        self._agent_options = agent_options
        self._llm_client = llm_client
        self._tool_executor = tool_executor
        self._session = session
        self._context_manager = context_manager
        self._on_close = on_close
        self._run_control: RunControl | None = None
        self._run_lock = asyncio.Lock()
        self._event_dispatcher = _EventDispatcher()
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
        model: LlmModel,
        max_turns: int = 50,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        effort: LlmThinkingEffort = LlmThinkingEffort.LOW,
    ) -> None:
        """运行一次 Agent，并在终态事件交付完成后返回。"""

        prompt = prompt.strip()
        if not prompt:
            raise ValueError("用户消息不能为空")
        run_control = await self._start_run(
            prompt=prompt,
            model=model,
            permission_mode=permission_mode,
            max_turns=max_turns,
            effort=effort,
        )
        await self._wait_for_control(run_control)

    async def resume(
        self,
        *,
        model: LlmModel,
        max_turns: int = 50,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        effort: LlmThinkingEffort = LlmThinkingEffort.LOW,
    ) -> None:
        """从 Session 最后一个完整且可继续的消息边界创建新 Run。"""

        run_control = await self._start_run(
            prompt=None,
            model=model,
            permission_mode=permission_mode,
            max_turns=max_turns,
            effort=effort,
        )
        await self._wait_for_control(run_control)

    def subscribe(self, listener: AgentEventListener) -> Unsubscribe:
        """订阅生命周期事件，并返回幂等取消订阅函数。"""

        if self._closed or self._closing:
            raise RuntimeError("Agent 已关闭")
        return self._event_dispatcher.subscribe(listener)

    async def steer(self, content: str) -> SteerResult:
        """把用户输入加入当前 Run 的 FIFO，在下一个安全点消费。"""

        value = content.strip()
        if not value:
            raise ValueError("运行中输入不能为空")
        if len(value) > self._agent_options.max_steering_chars:
            raise ValueError(f"运行中输入不能超过 {self._agent_options.max_steering_chars} 个字符")
        # 入队只在 Turn 边界被消费；accepting_input 与 _run_lock 共同构成关闭窗口。
        # deque 不是线程安全的，但只在这把锁内修改。
        async with self._run_lock:
            run_control = self._run_control
            if run_control is None or not run_control.accepting_input:
                raise AgentNotRunningError("当前 Agent 不接受运行中输入")
            if len(run_control.steering) >= self._agent_options.max_steering_messages:
                raise OverflowError("运行中输入队列已满")
            queued = QueuedInput(uuid4().hex, UserMessage(value))
            run_control.steering.append(queued)
            return SteerResult(queued.input_id, run_control.agent_run.run_id)

    async def abort(self) -> bool:
        """幂等中断当前 Run，并清空尚未消费的 steering。"""

        async with self._run_lock:
            run_control = self._run_control
            if run_control is None:
                return False
            if run_control.abort_requested:
                return True
            if run_control.agent_run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return False
            run_control.accepting_input = False
            run_control.abort_requested = True
            run_control.steering.clear()
            # abort 只是请求，不在这里落终态；终态统一由 Run 循环设置。
            task = run_control.owner_task
            if task is None:
                # owner_task 为空是启动窗口的防御分支，直接取消并清理登记。
                run_control.agent_run.cancel()
                run_control.done.set()
                self._run_control = None
            elif not task.done() and run_control.agent_run.status is RunStatus.RUNNING:
                # 只在 RUNNING 时 cancel；IDLE 启动窗口由执行体里的 abort_requested 检查兜住。
                task.cancel()
            return True

    async def wait_for_idle(self) -> None:
        """等待当前 Run 的终态事件和清理全部完成。"""

        run_control = self._run_control
        # 只等当下的 Run；之后启动的新 Run 不受这次等待约束。
        if run_control is not None:
            await run_control.done.wait()

    async def _start_run(
        self,
        *,
        prompt: str | None,
        model: LlmModel,
        permission_mode: PermissionMode,
        max_turns: int,
        effort: LlmThinkingEffort,
    ) -> RunControl:
        """原子登记活动 Run，并把执行任务交给 Agent 持有。"""

        if max_turns <= 0:
            raise ValueError("Agent 最大 Turn 数必须大于 0")
        if not self._llm_client.supports(model.provider):
            raise ValueError(f"未配置的 Provider：{model.provider}")
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
            agent_run = AgentRun(
                session_id=self._session_id,
                run_id=uuid4().hex,
                max_turns=max_turns,
            )
            run_control = RunControl(agent_run, asyncio.Event())
            # 先登记控制对象再创建任务，并全程持锁；锁外观察者看不到半登记状态。
            self._run_control = run_control
            # 任务所有权归 Agent；调用方取消自己的等待不会隐式接管或取消 Run。
            run_control.owner_task = asyncio.create_task(
                self._execute_run(
                    run_control,
                    prompt=prompt,
                    model=model,
                    permission_mode=permission_mode,
                    effort=effort,
                    initial_tool_results=initial_tool_results,
                )
            )
            return run_control

    async def _wait_for_control(self, run_control: RunControl) -> None:
        """等待 Agent 自己持有的任务，调用方取消不会隐式接管 Run。"""

        task = run_control.owner_task
        if task is None:
            raise RuntimeError("Agent Run 未正确启动")
        await asyncio.shield(task)

    async def _emit(self, agent_event: AgentEvent) -> None:
        """按订阅顺序交付事件，使慢消费者形成明确背压。"""

        await self._event_dispatcher.emit(agent_event)

    async def _execute_run(
        self,
        run_control: RunControl,
        *,
        prompt: str | None,
        model: LlmModel,
        permission_mode: PermissionMode,
        effort: LlmThinkingEffort,
        initial_tool_results: tuple[ToolResultMessage, ...] = (),
    ) -> None:
        """驱动一个已经登记的 Run，并确保唯一终态和状态清理。"""

        agent_run = run_control.agent_run
        agent_run.start()
        committer = SessionCommitter(self._session, agent_run, self._emit)
        turn_runner = TurnRunner(
            session=self._session,
            workspace=self._workspace,
            llm_client=self._llm_client,
            tool_executor=self._tool_executor,
            context_manager=self._context_manager,
            committer=committer,
            emit=self._emit,
            agent_run=agent_run,
        )
        error: str | None = None
        try:
            await self._emit(
                AgentStartEvent(
                    session_id=agent_run.session_id,
                    run_id=agent_run.run_id,
                    sequence=agent_run.next_sequence(),
                )
            )
            # 事件发出后才检查 abort，覆盖 status 仍为 IDLE 的启动窗口。
            if run_control.abort_requested:
                raise asyncio.CancelledError
            async with asyncio.timeout(self._agent_options.max_duration_seconds):
                for result in initial_tool_results:
                    await committer.emit_complete_message("resume-repair", result)
                pending_user: UserMessage | None = None
                if prompt is not None:
                    pending_user = UserMessage(prompt)
                while agent_run.status is RunStatus.RUNNING:
                    outcome = await turn_runner.run_turn(
                        model=model,
                        permission_mode=permission_mode,
                        effort=effort,
                        user_message=pending_user,
                    )
                    turn_reason = outcome.stop_reason
                    has_tool_calls = outcome.has_tool_calls
                    pending_user = None
                    # Turn 内部可能已 fail（会话/上下文/工具故障），不能覆盖更精确的终态原因。
                    if agent_run.status is not RunStatus.RUNNING:
                        continue
                    if turn_reason is LlmStopReason.LENGTH:
                        # 截断参数始终拒绝执行；连续失败只允许两次重新规划。
                        if agent_run.consecutive_length_recoveries >= 2 or not has_tool_calls:
                            agent_run.fail(StopReason.MODEL_LENGTH)
                            error = agent_run.length_error(has_tool_calls=has_tool_calls)
                            continue
                        agent_run.consecutive_length_recoveries += 1
                    else:
                        agent_run.consecutive_length_recoveries = 0
                    queued = await self._next_steering(run_control, turn_reason)
                    if queued is not None:
                        pending_user = queued.message
                    elif turn_reason is LlmStopReason.STOP:
                        # STOP 但队列非空时继续；只有 STOP 且队列排空才完成。
                        agent_run.complete()
        except TimeoutError as exc:
            # 异常分类顺序即优先级；已设置终态时不再覆盖原因。
            if agent_run.stop_reason is None:
                agent_run.fail(StopReason.TIMEOUT)
            error = str(exc) or "Agent 运行超时"
        except asyncio.CancelledError:
            agent_run.cancel()
            error = "Agent 运行已取消"
        except Exception as exc:
            if agent_run.stop_reason is None:
                agent_run.fail(StopReason.INTERNAL_ERROR)
            error = str(exc)
        finally:
            # 先关闭输入窗口，终态交付期间 steer 一律拒绝。
            run_control.accepting_input = False
            try:
                if agent_run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                    # 修复必须先于终态事件；修复失败不改状态，只补充 error 文本。
                    try:
                        await self._repair_dangling_tool_calls(committer)
                    except Exception as exc:
                        error = f"{error or '运行未完成'}；工具链修复失败：{exc}"
                status: AgentEndStatus = "failed"
                if agent_run.status is RunStatus.COMPLETED:
                    status = "completed"
                elif agent_run.status is RunStatus.CANCELLED:
                    status = "cancelled"
                await self._emit(
                    AgentEndEvent(
                        session_id=agent_run.session_id,
                        run_id=agent_run.run_id,
                        sequence=agent_run.next_sequence(),
                        status=status,
                        stop_reason=agent_run.stop_reason,
                        total_tokens=agent_run.total_tokens,
                        error=error,
                    )
                )
            finally:
                async with self._run_lock:
                    if self._run_control is run_control:
                        self._run_control = None
                # 先清登记再唤醒观察者：新 Run 可以启动早于旧 Run 的等待者被唤醒。
                run_control.done.set()

    async def _next_steering(
        self,
        run_control: RunControl,
        turn_reason: LlmStopReason | None,
    ) -> QueuedInput | None:
        """在 Turn 安全点消费一条输入，或原子关闭 Run 的输入窗口。"""

        async with self._run_lock:
            if run_control.steering:
                return run_control.steering.popleft()
            if turn_reason is LlmStopReason.STOP:
                run_control.accepting_input = False
            return None

    async def _repair_dangling_tool_calls(self, committer: SessionCommitter) -> None:
        """为中断时已落盘但没有结果的工具调用追加错误结果。"""

        messages = await self._session.messages()
        plan = analyze_resume(messages)
        for result in plan.missing_tool_results:
            # interruption-repair 是伪 Turn id，没有对应的 TurnStart 事件，仅用于消息坐标。
            await committer.emit_complete_message("interruption-repair", result)

    async def close(self) -> None:
        """幂等关闭 Agent 持有的外部连接。"""

        if self._closed:
            return
        # _closing 一旦置位不再复位；_closed 只在 on_close 成功后置位，失败后仍可重试。
        self._closing = True
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_resources())
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        """关闭等待者取消不会中断清理，失败后允许再次尝试。"""
        await self.abort()
        await self.wait_for_idle()
        self._event_dispatcher.clear()
        if self._on_close is not None:
            await self._on_close()
        self._closed = True
