from camel_harness.agent.session import SessionManager, SessionMetadata
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import (
    AssistantMessage,
    LlmStopReason,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)


async def test_jsonl_store_appends_and_projects_messages(tmp_path) -> None:
    store = JsonlSessionStorage(tmp_path)
    await store.create(SessionMetadata.create("session-1", "测试会话", str(tmp_path)))
    message = UserMessage("你好")
    await store.append_message("session-1", message)

    assert await store.messages("session-1") == (message,)
    line = (tmp_path / "session-1" / "messages.jsonl").read_text("utf-8").strip()
    assert '"role":"user"' in line
    assert '"session_id"' not in line


async def test_tool_results_remain_distinct_messages(tmp_path) -> None:
    store = JsonlSessionStorage(tmp_path)
    await store.create(SessionMetadata.create("session-1", "测试会话", str(tmp_path)))
    await store.append_message(
        "session-1",
        AssistantMessage(
            (
                ToolUseBlock("call-1", "read_file", {}),
                ToolUseBlock("call-2", "list_files", {}),
            ),
            LlmStopReason.TOOL_USE,
        ),
    )
    for call_id, name in (("call-1", "read_file"), ("call-2", "list_files")):
        await store.append_message(
            "session-1",
            ToolResultMessage(call_id, name, "ok"),
        )

    messages = await store.messages("session-1")

    assert len(messages) == 3
    assert isinstance(messages[0], AssistantMessage)
    assert isinstance(messages[0].content[0], ToolUseBlock)
    assert all(isinstance(message, ToolResultMessage) for message in messages[1:])
    assert [
        message.call_id for message in messages[1:] if isinstance(message, ToolResultMessage)
    ] == [
        "call-1",
        "call-2",
    ]


async def test_context_log_restores_compacted_surface_after_restart(tmp_path) -> None:
    root = tmp_path / "sessions"
    first = SessionManager(JsonlSessionStorage(root))
    await first.create("session-1", tmp_path, "压缩恢复")
    originals = tuple(
        UserMessage(f"消息 {index}")
        if index % 2 == 0
        else AssistantMessage((TextBlock(f"消息 {index}"),), LlmStopReason.STOP)
        for index in range(8)
    )
    for message in originals:
        await first.append_message("session-1", message)
    summary = UserMessage("[Compacted History]\n\n历史摘要")
    await first.replace_context("session-1", 0, 5, summary)

    restarted = SessionManager(JsonlSessionStorage(root))

    assert await restarted.messages("session-1") == originals
    assert await restarted.context_messages("session-1") == (
        summary,
        originals[6],
        originals[7],
    )
    rows = (root / "session-1" / "context.jsonl").read_text("utf-8").splitlines()
    assert len(rows) == 9
    assert '"messageIndex":0' in rows[0]
    assert '"message"' not in rows[0]
    assert '"op":"replace"' in rows[-1]
