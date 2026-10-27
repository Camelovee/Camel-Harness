"""子会话历史与待审批请求在父会话下正确归组。"""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from camel_backend.api import HttpDependencies, create_router, install_exception_handlers
from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.events import SubagentEvent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.session.types import TaskOutcome
from camel_harness.agent.tools import PermissionMode, PermissionRequest
from camel_harness.llm import AssistantMessage, LlmStopReason, ToolUseBlock


async def test_subagent_api_filters_branches_and_restores_terminal_after_restart(
    tmp_path: Path,
) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    parent = session_manager.bind("parent")
    first = await parent.append_message(
        AssistantMessage(
            (
                ToolUseBlock(
                    "one",
                    "delegate_task",
                    {
                        "task": "检查代码",
                        "agent_type": "explore",
                    },
                ),
            ),
            LlmStopReason.TOOL_USE,
        )
    )
    await session_manager.create(
        "child-one", tmp_path, "子会话", parent_session_id="parent", parent_tool_call_id="one"
    )
    await session_manager.create(
        "child-two",
        tmp_path,
        "子会话",
        parent_session_id="parent",
        parent_tool_call_id="other-branch",
    )
    outcome = TaskOutcome("failed", "max_turns", "达到轮数上限", "2026-10-27T00:00:00+00:00")
    await session_manager.update("child-one", task_outcome=outcome)
    # 后续普通元数据更新不能清除终态。
    await session_manager.update("child-one", name="已结束子任务")
    restarted = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))

    async def unused_builder(
        session_id: str, workspace: Path, agent_settings: AgentSettings
    ) -> Agent:
        raise AssertionError("读取历史不应创建 Agent")

    run_service = RunService(unused_builder, restarted, SettingsStore(tmp_path / "config.json"))
    dependencies = HttpDependencies(tmp_path)
    dependencies._session_manager = restarted
    dependencies._run_service = run_service
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_router(dependencies))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/sessions/parent/subagents")
        assert response.status_code == 200
        children = response.json()["subagents"]
        assert len(children) == 1
        assert children[0]["session_id"] == "child-one"
        assert children[0]["task"] == "检查代码"
        assert children[0]["task_outcome"]["error"] == "达到轮数上限"
        assert children[0]["status"] == "failed"
        assert (await client.get("/api/sessions/missing/subagents")).status_code == 404
        assert [
            item["session_id"] for item in (await client.get("/api/sessions")).json()["sessions"]
        ] == ["parent"]
        # 将新委派放到另一分支，旧子会话不能被错误挂到新委派上。
        await parent.branch(None)
        await parent.append_message(
            AssistantMessage(
                (
                    ToolUseBlock(
                        "other-branch",
                        "delegate_task",
                        {
                            "task": "新分支",
                            "agent_type": "plan",
                        },
                    ),
                ),
                LlmStopReason.TOOL_USE,
            )
        )
        children = (await client.get("/api/sessions/parent/subagents")).json()["subagents"]
        assert [item["session_id"] for item in children] == ["child-two"]
        assert children[0]["phase"] == "历史状态未记录"
        assert children[0]["task_outcome"] is None
        assert first


async def test_child_approval_keeps_real_identity_and_live_status(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    await session_manager.bind("parent").append_message(
        AssistantMessage(
            (ToolUseBlock("delegate", "delegate_task", {"task": "执行", "agent_type": "general"}),),
            LlmStopReason.TOOL_USE,
        )
    )
    await session_manager.create(
        "child", tmp_path, "子会话", parent_session_id="parent", parent_tool_call_id="delegate"
    )

    async def unused_builder(
        session_id: str, workspace: Path, agent_settings: AgentSettings
    ) -> Agent:
        raise AssertionError("查询状态不应创建 Agent")

    run_service = RunService(
        unused_builder, session_manager, SettingsStore(tmp_path / "config.json")
    )
    run_service._observe(
        "parent",
        SubagentEvent(
            session_id="parent",
            run_id="parent-run",
            sequence=8,
            turn_id="parent-turn",
            call_id="delegate",
            agent_type="general",
            event={
                "type": "approval_requested",
                "session_id": "child",
                "run_id": "child-run",
                "sequence": 5,
                "request_id": "request",
                "call_id": "shell",
            },
        ),
    )
    web_approval = WebApproval()
    task = asyncio.create_task(
        web_approval.approve(
            PermissionRequest(
                "run_shell",
                frozenset({"process.execute"}),
                "execute",
                {"command": "echo ok"},
                PermissionMode.DEFAULT,
                request_id="request",
                session_id="child",
                execution_context={"command": "echo ok", "working_directory": str(tmp_path)},
            ),
            "需要审批",
        )
    )
    await asyncio.sleep(0)
    dependencies = HttpDependencies(tmp_path)
    dependencies._session_manager = session_manager
    dependencies._run_service = run_service
    dependencies._web_approval = web_approval
    app = FastAPI()
    app.include_router(create_router(dependencies))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            approval = (await client.get("/api/approvals")).json()["approvals"][0]
            assert approval["session_id"] == "child"
            assert approval["parent_session_id"] == "parent"
            assert approval["parent_tool_call_id"] == "delegate"
            assert approval["request_id"] == "request"
            assert approval["execution_context"]["command"] == "echo ok"
            children = (await client.get("/api/sessions/parent/subagents")).json()["subagents"]
            assert children[0]["status"] == "waiting_approval"
            assert (
                await client.post("/api/approvals/request", json={"approved": True})
            ).status_code == 200
            assert (await client.get("/api/approvals")).json()["approvals"] == []
            assert await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
