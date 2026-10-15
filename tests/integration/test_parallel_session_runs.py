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


def _store(config: Path, **fields: object) -> SettingsStore:
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

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.started.set()
        await self.finish.wait()
        yield TextDelta(text="独立完成")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


async def test_sessions_stream_concurrently_and_abort_independently(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    store = _store(
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
        llm = LlmClient(adapter)
        registry = ToolRegistry()
        return Agent(
            sessions.bind(session_id),
            workspace,
            AgentOptions(),
            llm,
            registry,
            ToolExecutor(registry),
            ContextManager(CompactionEngine(llm)),
            system_prompt="测试提示",
        )

    runs = RunService(build, sessions, store)
    for session_id in ("a", "b"):
        await sessions.create(session_id, tmp_path, session_id)
    tasks: list[asyncio.Task[list[dict]]] = []

    async def consume(stream: AsyncIterator[str]) -> list[dict]:
        return [json.loads(frame.split("data: ", 1)[1]) async for frame in stream]

    try:
        for session_id in ("a", "b"):
            async with runs.acquire(session_id) as (agent, _model):
                stream = await open_agent_sse(
                    agent, lambda agent=agent: agent.run("独立任务", model=_MODEL)
                )
            tasks.append(asyncio.create_task(consume(stream)))
        await asyncio.wait_for(
            asyncio.gather(*(adapter.started.wait() for adapter in adapters.values())), 2
        )
        assert runs.is_running("a") and runs.is_running("b")
        with pytest.raises(AgentBusyError):
            async with runs.acquire("a") as _unused:
                pytest.fail("同一会话不能启动第二个 Run")
        await runs.abort("a")
        first = await asyncio.wait_for(tasks[0], 2)
        assert first[-1]["status"] == "cancelled"
        assert runs.is_running("b")
        adapters["b"].finish.set()
        second = await asyncio.wait_for(tasks[1], 2)
        assert second[-1]["status"] == "completed"
        assert {event["session_id"] for event in first} == {"a"}
        assert {event["session_id"] for event in second} == {"b"}
        assert len(await sessions.bind("a").messages()) == 1
        assert len(await sessions.bind("b").messages()) == 2
    finally:
        await runs.close()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("operation", ["update", "delete", "delete_model"])
async def test_session_mutations_wait_for_startup_and_recheck_active_run(tmp_path, operation):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    store = _store(
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
    await store.add_model(
        ModelInput(name="备用", provider="anthropic", model="fake-other", api_key="test-only-key")
    )
    snapshot = await store.snapshot()
    model_id = str(snapshot["models"][1]["id"])
    await sessions.create("locked", tmp_path, "互斥测试")
    await sessions.update("locked", model_id=model_id)
    adapter = GatedLlm()

    async def build(session_id, workspace, settings):
        llm = LlmClient(adapter)
        registry = ToolRegistry()
        return Agent(
            sessions.bind(session_id),
            workspace,
            AgentOptions(),
            llm,
            registry,
            ToolExecutor(registry),
            ContextManager(CompactionEngine(llm)),
            system_prompt="测试提示",
        )

    runs = RunService(build, sessions, store)
    service = SessionService(sessions, runs, store)
    settings_service = SettingsService(store, sessions, runs)
    task = None
    mutation = None
    try:
        async with runs.acquire("locked") as (agent, _model):
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
        assert (await sessions.get("locked")).model_id == model_id
        assert (await store.resolve(model_id)).model == "fake-other"
    finally:
        await runs.close()
        await asyncio.gather(*(item for item in (task, mutation) if item), return_exceptions=True)


async def test_delete_holds_startup_lock_until_session_is_removed(tmp_path, monkeypatch):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("delete", tmp_path, "删除测试")
    store = _store(
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

    runs = RunService(forbidden_build, sessions, store)
    service = SessionService(sessions, runs, store)
    deleting, proceed = asyncio.Event(), asyncio.Event()
    original_delete = sessions.delete

    async def gated_delete(session_id):
        deleting.set()
        await proceed.wait()
        return await original_delete(session_id)

    monkeypatch.setattr(sessions, "delete", gated_delete)
    delete = asyncio.create_task(service.delete_cascade("delete"))
    await deleting.wait()
    lookup = asyncio.create_task(runs.require_agent("delete"))
    await asyncio.sleep(0)
    assert not lookup.done()
    proceed.set()
    await delete
    with pytest.raises(KeyError):
        await lookup
