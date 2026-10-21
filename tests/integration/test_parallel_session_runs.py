import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from camel_backend.api.sse import open_agent_sse
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_harness import Agent, AgentBusyError, AgentOptions
from camel_harness.agent.config import AgentSettings, ModelInput
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    TextDelta,
)


def _settings_store(config: Path, **fields: object) -> SettingsStore:
    """把测试配置写入临时 config.json 后构造 SettingsStore。"""

    import json

    config.write_text(json.dumps(fields), encoding="utf-8")
    return SettingsStore(config)


_MODEL = LlmModel("anthropic", "fake-model")


class GatedLlm(LlmAdapter):
    """两个会话必须同时到达模型阶段，取消其中一个不释放另一个。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    @property
    def provider_id(self) -> str:
        return "anthropic"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.started.set()
        await self.finish.wait()
        yield TextDelta(text="独立完成")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


async def test_sessions_stream_concurrently_and_abort_independently(tmp_path: Path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    settings_store = _settings_store(
        tmp_path / "config.json",
        models=[
            {
                "id": "m1",
                "name": "测试模型",
                "provider": "anthropic",
                "model": "fake-model",
                "api_key": "test-only-key",
            }
        ],
        default_model_id="m1",
    )
    adapters: dict[str, GatedLlm] = {}

    async def build(session_id: str, workspace: Path, _: AgentSettings) -> Agent:
        adapter = GatedLlm()
        adapters[session_id] = adapter
        llm_client = LlmClient(adapter)
        tool_registry = ToolRegistry()
        return Agent(
            session=session_manager.bind(session_id),
            workspace=workspace,
            agent_options=AgentOptions(),
            llm_client=llm_client,
            tool_executor=ToolExecutor(tool_registry=tool_registry),
            context_manager=ContextManager(
                system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
            ),
        )

    run_service = RunService(
        build_agent=build, session_manager=session_manager, settings_store=settings_store
    )
    for session_id in ("a", "b"):
        await session_manager.create(session_id, tmp_path, session_id)
    tasks: list[asyncio.Task[list[dict]]] = []

    async def consume(stream: AsyncIterator[str]) -> list[dict]:
        return [json.loads(frame.split("data: ", 1)[1]) async for frame in stream]

    try:
        for session_id in ("a", "b"):
            async with run_service.acquire(session_id) as (agent, _model):
                stream = await open_agent_sse(
                    agent, lambda agent=agent: agent.run("独立任务", model=_MODEL)
                )
            tasks.append(asyncio.create_task(consume(stream)))
        await asyncio.wait_for(
            asyncio.gather(*(adapter.started.wait() for adapter in adapters.values())), 2
        )
        assert run_service.is_running("a") and run_service.is_running("b")
        with pytest.raises(AgentBusyError):
            async with run_service.acquire("a") as _unused:
                pytest.fail("同一会话不能启动第二个 Run")
        await run_service.abort("a")
        first = await asyncio.wait_for(tasks[0], 2)
        assert first[-1]["status"] == "cancelled"
        assert run_service.is_running("b")
        adapters["b"].finish.set()
        second = await asyncio.wait_for(tasks[1], 2)
        assert second[-1]["status"] == "completed"
        assert {event["session_id"] for event in first} == {"a"}
        assert {event["session_id"] for event in second} == {"b"}
        assert len(await session_manager.bind("a").messages()) == 1
        assert len(await session_manager.bind("b").messages()) == 2
    finally:
        await run_service.close()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("operation", ["update", "delete", "delete_model"])
async def test_session_mutations_wait_for_startup_and_recheck_active_run(tmp_path, operation):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    settings_store = _settings_store(
        tmp_path / "config.json",
        models=[
            {
                "id": "m1",
                "name": "测试模型",
                "provider": "anthropic",
                "model": "fake-model",
                "api_key": "test-only-key",
            }
        ],
        default_model_id="m1",
    )
    await settings_store.add_model(
        ModelInput(name="备用", provider="anthropic", model="fake-other", api_key="test-only-key")
    )
    snapshot = await settings_store.snapshot()
    model_id = str(snapshot["models"][1]["id"])
    await session_manager.create("locked", tmp_path, "互斥测试")
    await session_manager.update("locked", model_id=model_id)
    adapter = GatedLlm()

    async def build(session_id, workspace, settings):
        llm_client = LlmClient(adapter)
        tool_registry = ToolRegistry()
        return Agent(
            session=session_manager.bind(session_id),
            workspace=workspace,
            agent_options=AgentOptions(),
            llm_client=llm_client,
            tool_executor=ToolExecutor(tool_registry=tool_registry),
            context_manager=ContextManager(
                system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
            ),
        )

    run_service = RunService(
        build_agent=build, session_manager=session_manager, settings_store=settings_store
    )
    service = SessionService(
        session_manager=session_manager, run_service=run_service, settings_store=settings_store
    )
    settings_service = SettingsService(
        settings_store=settings_store, session_manager=session_manager, run_service=run_service
    )
    task = None
    mutation = None
    try:
        async with run_service.acquire("locked") as (agent, _model):
            if operation == "update":
                change = service.update("locked", model_id="environment")
            elif operation == "delete":
                change = service.delete_cascade("locked")
            else:
                change = settings_service.delete_model(model_id)
            mutation = asyncio.create_task(change)
            await asyncio.sleep(0)
            assert not mutation.done()
            task = asyncio.create_task(agent.run("保持运行", model=_MODEL))
            await asyncio.wait_for(adapter.started.wait(), 1)
        with pytest.raises(AgentBusyError):
            await asyncio.wait_for(mutation, 1)
        assert (await session_manager.get("locked")).model_id == model_id
        assert (await settings_store.resolve(model_id)).model == "fake-other"
    finally:
        await run_service.close()
        await asyncio.gather(*(item for item in (task, mutation) if item), return_exceptions=True)


async def test_delete_holds_startup_lock_until_session_is_removed(tmp_path, monkeypatch):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("delete", tmp_path, "删除测试")
    settings_store = _settings_store(
        tmp_path / "config.json",
        models=[
            {
                "id": "m1",
                "name": "测试模型",
                "provider": "anthropic",
                "model": "fake-model",
                "api_key": "test-only-key",
            }
        ],
        default_model_id="m1",
    )

    async def forbidden_build(*args):
        pytest.fail("删除过程不得重建 Agent")

    run_service = RunService(
        build_agent=forbidden_build, session_manager=session_manager, settings_store=settings_store
    )
    service = SessionService(
        session_manager=session_manager, run_service=run_service, settings_store=settings_store
    )
    deleting, proceed = asyncio.Event(), asyncio.Event()
    original_delete = session_manager.delete

    async def gated_delete(session_id):
        deleting.set()
        await proceed.wait()
        return await original_delete(session_id)

    monkeypatch.setattr(session_manager, "delete", gated_delete)
    delete = asyncio.create_task(service.delete_cascade("delete"))
    await deleting.wait()
    lookup = asyncio.create_task(run_service.require_agent("delete"))
    await asyncio.sleep(0)
    assert not lookup.done()
    proceed.set()
    await delete
    with pytest.raises(KeyError):
        await lookup
