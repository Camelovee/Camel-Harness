import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

from camel_backend.api.sse import encode_sse
from camel_harness import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmUsage,
    MessageCompleted,
    StreamStarted,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
    UsageUpdated,
)

_MODEL = LlmModel("fake", "requested")


class AuditedLlm(LlmAdapter):
    """首次调用读取工具，第二次回答；重复累计 usage 不得重复计费。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield StreamStarted(model="actual")
        first_turn = not any(
            isinstance(message, AssistantMessage) for message in llm_request.messages
        )
        if first_turn:
            yield ToolCallCompleted(ToolUseBlock("call", "read_file", {"path": "sample.txt"}))
        else:
            yield TextDelta(text="完成")
        for data in ({"input_tokens": 100}, {"output_tokens": 20}, {"output_tokens": 20}):
            yield UsageUpdated(LlmUsage(**data))
        yield MessageCompleted(
            LlmStopReason("tool_use" if first_turn else "stop"), model="actual-final"
        )


async def test_model_usage_and_large_result_reference_survive_restart(tmp_path) -> None:
    root = tmp_path / "sessions"
    (tmp_path / "sample.txt").write_text("大结果中文内容", "utf-8")
    session_manager = SessionManager(JsonlSessionStorage(root))
    await session_manager.create("session", tmp_path, "审计")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    llm_client = LlmClient(AuditedLlm())
    agent = Agent(
        session=session_manager.bind("session"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(
            tool_registry=tool_registry,
            normalize_result=FileToolResultStorage(session_manager.directory, 5).normalize,
        ),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("读取文件", model=_MODEL)
    assert events[-1].total_tokens == 240
    restarted = SessionManager(JsonlSessionStorage(root)).bind("session")
    messages = await restarted.messages()
    assistants = [message for message in messages if isinstance(message, AssistantMessage)]
    assert len(assistants) == 2
    for message in assistants:
        assert message.provider == "fake"
        assert message.model == "requested"
        assert message.response_model == "actual-final"
        assert message.usage is not None
        assert message.usage.total_tokens == 120
        assert message.usage.cache_read_tokens is None
    tool = next(message for message in messages if isinstance(message, ToolResultMessage))
    reference = tool.details["content_ref"]
    path = Path(reference["path"])
    assert path.parent == root / "session" / "tool-results"
    assert reference["size_bytes"] == len(path.read_bytes())
    assert reference["original_chars"] == len(path.read_text("utf-8"))
    assert reference["preview_chars"] == 5
    revision = hashlib.sha256("大结果中文内容".encode()).hexdigest()
    assert path.read_text("utf-8") == f"文件版本：{revision}\n1: 大结果中文内容"
    assert (await restarted.context_snapshot()).messages == messages
    for event in events:
        if event.type == "message_end" and event.message.role in {"assistant", "toolResult"}:
            wire = json.loads(encode_sse(event).split("data: ")[1])["message"]
            if event.message.role == "assistant":
                assert wire["model"] == "requested"
                assert wire["usage"]["total_tokens"] == 120
            else:
                assert wire["details"]["content_ref"] == reference
