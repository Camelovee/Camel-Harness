import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from camel_harness.agent.config import CompactionSettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionCompactionEvent, SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    TextDelta,
    UserMessage,
)


async def test_shared_context_prepares_and_commits_independent_sessions_concurrently(
    tmp_path: Path,
) -> None:
    """同一上下文实例按次借用会话，不保存被最后一个请求覆盖的绑定。"""
    names = ("会话甲", "会话乙")

    class ConcurrentSummaryLlm(LlmAdapter):
        def __init__(self) -> None:
            self.requests: list[LlmRequest] = []
            self.all_started = asyncio.Event()

        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(llm_request)
            if len(self.requests) == len(names):
                self.all_started.set()
            await self.all_started.wait()
            transcript = llm_request.messages[0]
            assert isinstance(transcript, UserMessage)
            selected = [name for name in names if name in transcript.content]
            assert len(selected) == 1
            yield TextDelta(selected[0] + "摘要")
            yield MessageCompleted(LlmStopReason.STOP)

    adapter = ConcurrentSummaryLlm()
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    sessions = []
    originals = []
    for index, name in enumerate(names):
        session_id = f"session-{index}"
        await session_manager.create(session_id, tmp_path, name)
        session = session_manager.bind(session_id)
        for _ in range(8):
            await session.append_message(UserMessage(name + "原文" * 150))
        sessions.append(session)
        originals.append(await session.context_snapshot())
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(
            llm_client=LlmClient(adapter),
            compaction_settings=CompactionSettings(
                reserve_tokens=127000,
                keep_recent_tokens=128,
                max_output_tokens=64,
                retry_max_output_tokens=128,
            ),
        ),
        system_prompt="基础角色",
    )
    model = LlmModel("fake", "m")
    prepared_contexts = await asyncio.wait_for(
        asyncio.gather(
            *(
                context_manager.prepare(session, model, workspace=tmp_path)
                for name, session in zip(names, sessions, strict=True)
            )
        ),
        timeout=5,
    )
    for index, (name, session, prepared) in enumerate(
        zip(names, sessions, prepared_contexts, strict=True)
    ):
        snapshot = await session.context_snapshot()
        assert prepared.system_prompt == "基础角色"
        assert prepared.messages == snapshot.messages
        assert prepared.messages[0] == UserMessage("[Compacted History]\n\n" + name + "摘要")
        assert all(
            isinstance(message, UserMessage) and name in message.content
            for message in prepared.messages
        )
        entries = await session.entries()
        assert len(entries) == 9
        assert isinstance(entries[-1], SessionCompactionEvent)
        assert originals[index].messages == tuple(entry.message for entry in entries[:-1])
        reloaded = session_manager.bind(session.session_id)
        assert prepared.messages == (await reloaded.context_snapshot()).messages
        assert len(await session.messages()) == 8
