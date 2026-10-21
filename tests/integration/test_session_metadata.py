import json

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import AssistantMessage, LlmStopReason, TextBlock, UserMessage


async def test_session_metadata_crud_and_file_layout(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))

    created = await session_manager.create("session-1", tmp_path, "初始名称")

    assert created.name == "初始名称"
    assert created.workspace == str(tmp_path.resolve())
    assert created.created_at == created.updated_at
    assert (root / "session-1" / "session.jsonl").exists()
    assert len((root / "session-1" / "session.jsonl").read_text("utf-8").splitlines()) == 1

    updated = await session_manager.update("session-1", name="新名称", archived=True)

    assert updated.name == "新名称"
    assert updated.archived is True
    assert updated.created_at == created.created_at
    assert (await session_manager.get("session-1")) == updated
    listed = await session_manager.list()
    assert tuple(item.session_id for item in listed) == ("session-1",)

    assert await session_manager.delete("session-1") is True
    assert await session_manager.get("session-1") is None
    assert not (root / "session-1").exists()


async def test_appending_messages_updates_metadata(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    await session_manager.create("session-1", tmp_path, "批量写入")
    await session_manager.bind("session-1").append_message(UserMessage("问题"))
    await session_manager.bind("session-1").append_message(
        AssistantMessage((TextBlock("回答"),), LlmStopReason.STOP)
    )

    metadata = await session_manager.get("session-1")
    assert metadata is not None
    assert metadata.message_count == 2
    assert metadata.last_message_type == "assistant"
    rows = [
        json.loads(line)
        for line in (root / "session-1" / "session.jsonl").read_text("utf-8").splitlines()
    ]
    assert [row["type"] for row in rows] == ["session", "message", "message"]
    assert rows[0]["id"] == "session-1"
    assert rows[0]["cwd"] == str(tmp_path.resolve())


async def test_global_session_list_can_be_filtered_by_workspace(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "global-sessions"))
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    await session_manager.create("first-session", first_workspace, "第一个工作区")
    await session_manager.create("second-session", second_workspace, "第二个工作区")
    all_sessions = await session_manager.list()
    assert {item.session_id for item in all_sessions} == {
        "first-session",
        "second-session",
    }
    filtered = tuple(
        item for item in all_sessions if item.workspace == str(first_workspace.resolve())
    )
    assert tuple(item.session_id for item in filtered) == ("first-session",)


async def test_child_sessions_record_parent_and_delete_with_parent(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    await session_manager.create("parent", tmp_path, "父会话")
    await session_manager.create("other", tmp_path, "其他会话")
    child = await session_manager.create(
        "child",
        tmp_path,
        "子会话",
        parent_session_id="parent",
        parent_tool_call_id="tool-1",
    )
    await session_manager.create("grandchild", tmp_path, "孙会话", parent_session_id="child")
    await session_manager.bind("child").append_message(UserMessage("子任务"))

    assert child.parent_session_id == "parent"
    assert child.parent_tool_call_id == "tool-1"
    restarted_session_manager = SessionManager(JsonlSessionStorage(root))
    reloaded = await restarted_session_manager.get("child")
    assert reloaded is not None
    assert reloaded.parent_session_id == "parent"
    assert reloaded.parent_tool_call_id == "tool-1"
    assert {item.session_id for item in await restarted_session_manager.list()} == {
        "parent",
        "other",
        "child",
        "grandchild",
    }
    child_path = root / "parent" / "subagents" / "child" / "session.jsonl"
    grandchild_path = child_path.parent / "subagents" / "grandchild" / "session.jsonl"
    stored = json.loads(child_path.read_text("utf-8").splitlines()[0])
    assert stored["parentSessionId"] == "parent"
    assert stored["parentToolCallId"] == "tool-1"
    assert grandchild_path.exists()
    assert await restarted_session_manager.deletion_order("parent") == (
        "grandchild",
        "child",
        "parent",
    )

    assert await restarted_session_manager.delete("parent") is True
    for session_id in ("parent", "child", "grandchild"):
        assert await session_manager.get(session_id) is None
    assert not (root / "parent").exists()
    assert await session_manager.get("other") is not None


async def test_child_delete_keeps_parent_session(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    await session_manager.create("parent", tmp_path, "父会话")
    await session_manager.create("child", tmp_path, "子会话", parent_session_id="parent")

    assert await session_manager.delete("child") is True
    assert (root / "parent" / "session.jsonl").exists()
    assert not (root / "parent" / "subagents").exists()


async def test_child_session_requires_existing_parent_in_same_workspace(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "global-sessions"))
    with pytest.raises(ValueError, match="父会话不存在"):
        await session_manager.create("child", tmp_path, "子会话", parent_session_id="missing")

    await session_manager.create("parent", tmp_path, "父会话")
    with pytest.raises(ValueError, match="工作目录"):
        await session_manager.create(
            "child", tmp_path / "elsewhere", "子会话", parent_session_id="parent"
        )


async def test_session_file_symlink_cannot_escape_global_root(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escaped").symlink_to(outside, target_is_directory=True)
    session_storage = JsonlSessionStorage(root)

    with pytest.raises(ValueError, match="符号链接"):
        await session_storage.append_message("escaped", UserMessage("越界"))

    assert not (outside / "session.jsonl").exists()
