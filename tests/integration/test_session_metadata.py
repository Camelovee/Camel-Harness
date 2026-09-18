import json

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import AssistantMessage, LlmStopReason, TextBlock, UserMessage


async def test_session_metadata_crud_and_file_layout(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    sessions = SessionManager(JsonlSessionStorage(root))

    created = await sessions.create("session-1", tmp_path, "初始名称")

    assert created.name == "初始名称"
    assert created.workspace == str(tmp_path.resolve())
    assert created.created_at == created.updated_at
    assert (root / "session-1" / "metadata.json").exists()
    assert not (root / "session-1" / "messages.jsonl").exists()

    updated = await sessions.update("session-1", name="新名称", archived=True)

    assert updated.name == "新名称"
    assert updated.archived is True
    assert updated.created_at == created.created_at
    assert (await sessions.get("session-1")) == updated
    listed = await sessions.list()
    assert tuple(item.session_id for item in listed) == ("session-1",)

    assert await sessions.delete("session-1") is True
    assert await sessions.get("session-1") is None
    assert not (root / "session-1").exists()


async def test_appending_messages_updates_metadata(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    sessions = SessionManager(JsonlSessionStorage(root))
    await sessions.create("session-1", tmp_path, "批量写入")
    await sessions.append_message("session-1", UserMessage("问题"))
    await sessions.append_message(
        "session-1", AssistantMessage((TextBlock("回答"),), LlmStopReason.STOP)
    )

    metadata = await sessions.get("session-1")
    assert metadata is not None
    assert metadata.message_count == 2
    assert metadata.last_message_type == "assistant"
    session_dir = root / "session-1"
    assert len((session_dir / "messages.jsonl").read_text("utf-8").splitlines()) == 2
    persisted = json.loads((session_dir / "metadata.json").read_text("utf-8"))
    assert persisted["messageCount"] == 2
    assert persisted["id"] == "session-1"
    assert persisted["cwd"] == str(tmp_path.resolve())


async def test_global_session_list_can_be_filtered_by_workspace(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "global-sessions"))
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    await sessions.create("first-session", first_workspace, "第一个工作区")
    await sessions.create("second-session", second_workspace, "第二个工作区")
    all_sessions = await sessions.list()
    assert {item.session_id for item in all_sessions} == {
        "first-session",
        "second-session",
    }
    filtered = tuple(
        item for item in all_sessions if item.workspace == str(first_workspace.resolve())
    )
    assert tuple(item.session_id for item in filtered) == ("first-session",)


async def test_session_directory_symlink_cannot_escape_global_root(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escaped").symlink_to(outside, target_is_directory=True)
    repository = JsonlSessionStorage(root)

    with pytest.raises(ValueError, match="符号链接"):
        await repository.append_message("escaped", UserMessage("越界"))

    assert not (outside / "messages.jsonl").exists()
