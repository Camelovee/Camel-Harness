from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent, AgentBusyError, AgentNotRunningError
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.events import AgentEvent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.state import SteerResult
from camel_harness.llm import AssistantMessage, LlmModel, ToolUseBlock

AgentBuilder = Callable[[str, Path, AgentSettings], Awaitable[Agent]]


class RunService:
    """按 Session 惰性定位唯一 Agent，并持有全部运行期生命周期规则。"""

    def __init__(
        self,
        build_agent: AgentBuilder,
        session_manager: SessionManager,
        settings_store: SettingsStore,
    ) -> None:
        self._build_agent = build_agent
        self._session_manager = session_manager
        self._settings_store = settings_store
        self._agents_by_session_id: dict[str, Agent] = {}
        self._lock = asyncio.Lock()
        self._subagents_by_parent: dict[str, dict[str, dict[str, Any]]] = {}

    def _observe(self, session_id: str, agent_event: AgentEvent) -> None:
        """只维护子任务当前视图，完整过程由子会话消息保存。"""

        if agent_event.type == "subagent_event":
            child = agent_event.event
            child_id = str(child["session_id"])
            snapshots = self._subagents_by_parent.setdefault(session_id, {})
            snapshot = snapshots.setdefault(
                child_id, {"status": "running", "turn": 0, "phase": "正在启动"}
            )
            kind = child["type"]
            if kind == "turn_start":
                snapshot.update(phase="分析中", turn=child["turn"])
            elif kind == "message_update":
                update = child["assistant_event"]
                if update["type"] in {"text_delta", "reasoning_delta"}:
                    snapshot["phase"] = "生成回答" if update["type"] == "text_delta" else "思考中"
            elif kind == "tool_execution_start":
                snapshot["phase"] = f"调用 {child['name']}"
            elif kind == "approval_requested":
                snapshot.update(status="waiting_approval", phase="等待工具审批")
            elif kind == "approval_resolved":
                snapshot.update(
                    status="running", phase="已批准，执行中" if child["approved"] else "未获批准"
                )
            elif kind == "agent_end":
                snapshot.update(
                    status=child["status"],
                    phase="已结束",
                    stop_reason=child["stop_reason"],
                    error=child["error"],
                )
        elif agent_event.type == "agent_end":
            for snapshot in self._subagents_by_parent.get(session_id, {}).values():
                if snapshot["status"] in {"running", "waiting_approval"}:
                    snapshot.update(status="unknown", phase="运行已停止，结果未知")

    async def subagents(self, session_id: str) -> list[dict[str, Any]]:
        """按父会话当前分支的委派调用查询子任务，避免混入其他分支。"""

        if await self._session_manager.get(session_id) is None:
            raise KeyError("会话不存在")
        calls = {
            block.call_id: block
            for message in await self._session_manager.bind(session_id).messages()
            if isinstance(message, AssistantMessage)
            for block in message.content
            if isinstance(block, ToolUseBlock) and block.name == "delegate_task"
        }
        result: list[dict[str, Any]] = []
        for metadata in await self._session_manager.list():
            if metadata.parent_session_id != session_id:
                continue
            call = calls.get(metadata.parent_tool_call_id or "")
            if call is None:
                continue
            outcome = metadata.task_outcome
            snapshot = self._subagents_by_parent.get(session_id, {}).get(metadata.session_id, {})
            result.append(
                {
                    "session_id": metadata.session_id,
                    "parent_session_id": session_id,
                    "parent_tool_call_id": metadata.parent_tool_call_id,
                    "agent_type": call.arguments.get("agent_type", "未知"),
                    "task": call.arguments.get("task", ""),
                    "status": outcome.status if outcome else snapshot.get("status", "unknown"),
                    "phase": "已结束" if outcome else snapshot.get("phase", "历史状态未记录"),
                    "turn": snapshot.get("turn", 0),
                    "task_outcome": asdict(outcome) if outcome else None,
                }
            )
        return result

    async def require_agent(self, session_id: str, model_id: str | None = None) -> Agent:
        """返回绑定指定 Session 的唯一 Agent，必要时惰性创建。"""
        async with self._lock:
            agent, _ = await self._require_agent_locked(session_id, model_id)
            return agent

    @asynccontextmanager
    async def acquire(
        self, session_id: str, model_id: str | None = None
    ) -> AsyncIterator[tuple[Agent, LlmModel]]:
        """返回可启动 Run 的 Agent 与解析后的模型目标；互斥保持到首事件交付。"""
        async with self._lock:
            agent, model = await self._require_agent_locked(session_id, model_id)
            if agent.is_running:
                raise AgentBusyError("会话已有正在运行的 Agent")
            if model_id:
                await self._session_manager.update(session_id, model_id=model_id)
            yield agent, model

    async def steer(self, session_id: str, content: str) -> SteerResult:
        """向活动 Run 追加输入；不存在活动 Run 时拒绝。"""
        agent = self._agents_by_session_id.get(session_id)
        if agent is None:
            raise AgentNotRunningError("会话没有活动 Run")
        return await agent.steer(content)

    async def abort(self, session_id: str) -> None:
        """请求中断活动 Run；不存在或已经结束时不视为成功。"""
        agent = self._agents_by_session_id.get(session_id)
        if agent is None or not await agent.abort():
            raise AgentNotRunningError("会话没有活动 Run")

    def is_running(self, session_id: str) -> bool:
        """返回该会话当前是否存在活动 Run。"""
        agent = self._agents_by_session_id.get(session_id)
        return agent is not None and agent.is_running

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        """入口写用例共用启动锁，覆盖检查、改绑或删除的完整区间。"""
        async with self._lock:
            yield

    async def release_idle_locked(self, session_id: str) -> bool:
        """仅在 mutation 内使用，避免释放与会话删除之间重新创建 Agent。"""
        agent = self._agents_by_session_id.get(session_id)
        if agent is None:
            return True
        if agent.is_running:
            return False
        self._agents_by_session_id.pop(session_id)
        await agent.close()
        return True

    async def close(self) -> None:
        """关闭所有按需创建的 Agent。"""
        async with self._lock:
            agents = tuple(self._agents_by_session_id.values())
            self._agents_by_session_id.clear()
        await asyncio.gather(*(agent.close() for agent in agents))

    async def _require_agent_locked(
        self, session_id: str, model_id: str | None
    ) -> tuple[Agent, LlmModel]:
        metadata = await self._session_manager.get(session_id)
        if metadata is None:
            raise KeyError(f"会话不存在：{session_id}")
        model = await self._settings_store.resolve(model_id or metadata.model_id)
        current = self._agents_by_session_id.get(session_id)
        if current is not None:
            return current, model
        # 模型是 per-run 输入，Agent 只随非模型配置构建一次，不随切换重建。
        agent = await self._build_agent(
            session_id, Path(metadata.workspace), await self._settings_store.base_settings()
        )
        agent.subscribe(lambda event: self._observe(session_id, event))
        self._agents_by_session_id[session_id] = agent
        return agent, model
