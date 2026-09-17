from camel_harness.harness.session import SessionManager, SessionMetadata
from camel_harness.harness.session.jsonl import JsonlSessionStorage
from camel_harness.types import (
    Message,
    MessageRole,
    ToolResultBlock,
    ToolUseBlock,
)


async def test_jsonl_store_appends_and_projects_messages(tmp_path) -> None:
    store = JsonlSessionStorage(tmp_path)
    await store.create(SessionMetadata.create("session-1", "测试会话", str(tmp_path)))
    message = Message.text(MessageRole.USER, "你好")
    await store.append_message("session-1", message)

    assert await store.messages("session-1") == (message,)
    line = (tmp_path / "session-1" / "messages.jsonl").read_text("utf-8").strip()
    assert '"role":"user"' in line
    assert '"session_id"' not in line


async def test_adjacent_tool_results_are_projected_as_one_message(tmp_path) -> None:
    store = JsonlSessionStorage(tmp_path)
    await store.create(SessionMetadata.create("session-1", "测试会话", str(tmp_path)))
    await store.append_message(
        "session-1",
        Message(
            MessageRole.ASSISTANT,
            (
                ToolUseBlock("call-1", "read_file", {}),
                ToolUseBlock("call-2", "list_files", {}),
            ),
        ),
    )
    for call_id, name in (("call-1", "read_file"), ("call-2", "list_files")):
        await store.append_message(
            "session-1",
            Message(
                MessageRole.TOOL,
                (ToolResultBlock(call_id, name, "ok", False),),
            ),
        )

    messages = await store.messages("session-1")

    assert len(messages) == 2
    assert messages[0].role is MessageRole.ASSISTANT
    assert isinstance(messages[0].content[0], ToolUseBlock)
    assert messages[1].role is MessageRole.TOOL
    assert [block.call_id for block in messages[1].content] == ["call-1", "call-2"]
    assert all(isinstance(block, ToolResultBlock) for block in messages[1].content)


async def test_context_log_restores_compacted_surface_after_restart(tmp_path) -> None:
    root = tmp_path / "sessions"
    first = SessionManager(JsonlSessionStorage(root))
    await first.create("session-1", tmp_path, "压缩恢复")
    originals = tuple(
        Message.text(
            MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            f"消息 {index}",
        )
        for index in range(8)
    )
    for message in originals:
        await first.append_message("session-1", message)
    summary = Message.text(MessageRole.USER, "[Compacted History]\n\n历史摘要")
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
