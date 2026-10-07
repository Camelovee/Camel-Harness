import asyncio
import json
import stat

import pytest

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
    rows = [
        json.loads(line)
        for line in (tmp_path / "session-1" / "session.jsonl").read_text("utf-8").splitlines()
    ]
    assert [row["type"] for row in rows] == ["session", "message"]
    assert rows[1]["message"] == {"role": "user", "content": "你好"}
    assert "session_id" not in rows[1]["message"]


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
    rows = [
        json.loads(line)
        for line in (root / "session-1" / "session.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(rows) == 10
    assert [row["type"] for row in rows] == ["session", *(["message"] * 8), "compaction"]
    assert rows[-1]["summary"]["content"].startswith("[Compacted History]")


async def test_metadata_update_does_not_shift_compaction_coordinates(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path))
    await sessions.create("session-1", tmp_path, "初始名称")
    await sessions.append_message("session-1", UserMessage("旧消息"))
    await sessions.update("session-1", name="新名称", archived=True)
    await sessions.append_message("session-1", UserMessage("新消息"))
    await sessions.replace_context("session-1", 0, 0, UserMessage("摘要"))

    restarted = SessionManager(JsonlSessionStorage(tmp_path))
    assert await restarted.context_messages("session-1") == (
        UserMessage("摘要"),
        UserMessage("新消息"),
    )
    metadata = await restarted.get("session-1")
    assert metadata is not None
    assert metadata.name == "新名称"
    assert metadata.archived is True
    assert metadata.message_count == 2


async def test_incomplete_tail_is_rejected_without_appending(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path))
    await sessions.create("session-1", tmp_path, "测试会话")
    path = tmp_path / "session-1" / "session.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"message"')
    original = path.read_bytes()

    with pytest.raises(ValueError, match="尾部不完整"):
        await sessions.append_message("session-1", UserMessage("不能写入"))
    assert path.read_bytes() == original


async def test_session_file_and_directory_are_private(tmp_path) -> None:
    root = tmp_path / "sessions"
    sessions = SessionManager(JsonlSessionStorage(root))
    await sessions.create("session-1", tmp_path, "私有会话")

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "session-1").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "session-1" / "session.jsonl").stat().st_mode) == 0o600


async def test_corrupt_session_does_not_hide_healthy_sessions(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path))
    await sessions.create("healthy", tmp_path, "正常会话")
    await sessions.create("damaged", tmp_path, "损坏会话")
    with (tmp_path / "damaged" / "session.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"type":"message"')

    assert [item.session_id for item in await sessions.list()] == ["healthy"]
    with pytest.raises(ValueError, match="尾部不完整"):
        await sessions.get("damaged")


async def test_concurrent_metadata_updates_preserve_independent_fields(tmp_path) -> None:
    first = SessionManager(JsonlSessionStorage(tmp_path))
    second = SessionManager(JsonlSessionStorage(tmp_path))
    await first.create("session-1", tmp_path, "初始名称")

    await asyncio.gather(
        first.update("session-1", name="新名称"),
        second.update("session-1", archived=True),
    )

    metadata = await first.get("session-1")
    assert metadata is not None
    assert metadata.name == "新名称"
    assert metadata.archived is True
