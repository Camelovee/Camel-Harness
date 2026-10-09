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


async def test_context_snapshot_restores_compacted_surface_after_restart(tmp_path) -> None:
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
        await first.bind("session-1").append_message(message)
    summary = UserMessage("[Compacted History]\n\n历史摘要")
    snapshot = await first.bind("session-1").context_snapshot()
    await first.bind("session-1").compact("历史摘要", snapshot.nodes[6].entry_id, snapshot=snapshot)

    restarted = SessionManager(JsonlSessionStorage(root))

    assert await restarted.bind("session-1").messages() == originals
    assert (await restarted.bind("session-1").context_snapshot()).messages == (
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
    assert rows[-1]["summary"] == "历史摘要"
    assert rows[-1]["firstKeptEntryId"] == rows[7]["id"]


async def test_metadata_update_does_not_shift_compaction_coordinates(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path))
    await sessions.create("session-1", tmp_path, "初始名称")
    await sessions.bind("session-1").append_message(UserMessage("旧消息"))
    await sessions.update("session-1", name="新名称", archived=True)
    await sessions.bind("session-1").append_message(UserMessage("新消息"))
    snapshot = await sessions.bind("session-1").context_snapshot()
    await sessions.bind("session-1").compact("摘要", snapshot.nodes[1].entry_id, snapshot=snapshot)

    restarted = SessionManager(JsonlSessionStorage(tmp_path))
    assert (await restarted.bind("session-1").context_snapshot()).messages == (
        UserMessage("[Compacted History]\n\n摘要"),
        UserMessage("新消息"),
    )
    metadata = await restarted.get("session-1")
    assert metadata is not None
    assert metadata.name == "新名称"
    assert metadata.archived is True
    assert metadata.message_count == 2


async def test_compaction_rejects_stale_snapshot_after_another_writer(tmp_path) -> None:
    """校验生成摘要时的版本，而不是提交时重新加载得到的版本。"""
    first = SessionManager(JsonlSessionStorage(tmp_path))
    await first.create("session", tmp_path, "并发测试")
    session = first.bind("session")
    await session.append_message(UserMessage("旧消息"))
    snapshot = await session.context_snapshot()
    second = SessionManager(JsonlSessionStorage(tmp_path)).bind("session")
    await second.append_message(UserMessage("并发新增"))
    path = first.directory("session") / "session.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="并发修改"):
        await session.compact("过期摘要", snapshot.nodes[0].entry_id, snapshot=snapshot)
    assert path.read_bytes() == before
    assert snapshot.messages == (UserMessage("旧消息"),)
    assert (await second.context_snapshot()).messages == (
        UserMessage("旧消息"),
        UserMessage("并发新增"),
    )


async def test_compaction_returns_durable_snapshot_and_rejects_reuse(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("session", tmp_path, "压缩测试")
    session = manager.bind("session")
    await session.append_message(UserMessage("旧消息"))
    await session.append_message(UserMessage("近期消息"))
    snapshot = await session.context_snapshot()
    # 元数据更新不改变上下文，因此不应使压缩计划失效。
    await manager.update("session", name="新名称")
    updated = await session.compact("摘要", snapshot.nodes[1].entry_id, snapshot=snapshot)
    assert updated.messages == (UserMessage("[Compacted History]\n\n摘要"), UserMessage("近期消息"))
    assert updated.revision != snapshot.revision
    assert updated.leaf_id == updated.revision
    assert (
        updated
        == await SessionManager(JsonlSessionStorage(tmp_path)).bind("session").context_snapshot()
    )
    assert snapshot.messages == (UserMessage("旧消息"), UserMessage("近期消息"))
    with pytest.raises(ValueError, match="并发修改"):
        await session.compact("重复摘要", snapshot.nodes[1].entry_id, snapshot=snapshot)


async def test_competing_compactions_commit_only_once(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("session", tmp_path, "并发压缩")
    first = manager.bind("session")
    second = SessionManager(JsonlSessionStorage(tmp_path)).bind("session")
    await first.append_message(UserMessage("旧消息"))
    kept_id = await first.append_message(UserMessage("保留消息"))
    snapshot = await first.context_snapshot()
    outcomes = await asyncio.gather(
        first.compact("摘要 A", kept_id, snapshot=snapshot),
        second.compact("摘要 B", kept_id, snapshot=snapshot),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1
    assert (await first.context_snapshot()).revision != snapshot.revision
    rows = (manager.directory("session") / "session.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(row)["type"] for row in rows] == [
        "session",
        "message",
        "message",
        "compaction",
    ]


async def test_invalid_compaction_never_appends(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("session", tmp_path, "区间校验")
    session = manager.bind("session")
    await session.append_message(UserMessage("消息"))
    path = manager.directory("session") / "session.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="可见上下文"):
        await session.compact("摘要", "missing-entry", snapshot=await session.context_snapshot())
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "bad_record",
    [
        {"type": "message", "timestamp": "now"},
        {
            "type": "session_info",
            "timestamp": "now",
            "name": 3,
            "cwd": "/workspace",
            "archived": False,
        },
        {"type": "unknown", "timestamp": "now"},
    ],
)
async def test_malformed_typed_event_does_not_break_other_sessions(tmp_path, bad_record) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("bad", tmp_path, "损坏会话")
    await manager.create("good", tmp_path, "正常会话")
    path = manager.directory("bad") / "session.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(bad_record) + "\n")
    assert [item.session_id for item in await manager.list()] == ["good"]
    with pytest.raises(ValueError, match="会话日志损坏"):
        await manager.bind("bad").messages()


async def test_incomplete_tail_is_rejected_without_appending(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path))
    await sessions.create("session-1", tmp_path, "测试会话")
    path = tmp_path / "session-1" / "session.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"message"')
    original = path.read_bytes()

    with pytest.raises(ValueError, match="尾部不完整"):
        await sessions.bind("session-1").append_message(UserMessage("不能写入"))
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


async def test_bound_session_isolated_and_missing_binding_does_not_create(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    with pytest.raises(KeyError, match="会话不存在"):
        manager.bind("missing")
    assert not (tmp_path / "sessions").exists()
    await manager.create("first", tmp_path, "第一个会话")
    await manager.create("second", tmp_path, "第二个会话")
    first = manager.bind("first")
    second = manager.bind("second")
    assert first.session_id == "first"
    assert second.session_id == "second"
    await first.append_message(UserMessage("只属于第一个会话"))
    assert await second.messages() == ()
    assert await first.messages() == (UserMessage("只属于第一个会话"),)
    await manager.delete("first")
    with pytest.raises(KeyError, match="会话不存在"):
        await first.append_message(UserMessage("不自动重建"))
    assert await manager.get("first") is None
    assert await second.messages() == ()
