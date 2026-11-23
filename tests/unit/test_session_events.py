from dataclasses import FrozenInstanceError, fields
from typing import get_args

import pytest

from camel_harness.agent.session import (
    ApprovalDecision,
    ContextSnapshot,
    RunSummary,
    SessionCompactionEvent,
    SessionEvent,
    SessionHeaderEvent,
    SessionInfoEvent,
    SessionMessageEvent,
    SessionRunEvent,
)
from camel_harness.agent.session.events import (
    event_from_dict,
    event_to_dict,
    project_context,
    project_metadata,
)
from camel_harness.llm import AssistantMessage, LlmStopReason, LlmUsage, TextBlock, UserMessage


def _messages(count: int) -> tuple[SessionMessageEvent, ...]:
    return tuple(
        SessionMessageEvent(
            "now",
            UserMessage(f"消息 {index}"),
            id=f"m{index}",
            parent_id=f"m{index - 1}" if index else None,
        )
        for index in range(count)
    )


def test_context_replays_messages_and_compaction() -> None:
    events = _messages(4)
    compacted = SessionCompactionEvent("now", "摘要", "m3", id="c1", parent_id="m3")
    snapshot = project_context((*events, compacted))
    assert snapshot.messages == (UserMessage("[Compacted History]\n\n摘要"), events[3].message)
    assert snapshot.leaf_id == snapshot.revision == "c1"
    assert tuple(node.entry_id for node in snapshot.nodes) == ("c1", "m3")


@pytest.mark.parametrize("boundary", ["future", "m0"])
def test_context_rejects_invalid_kept_entry(boundary: str) -> None:
    with pytest.raises(ValueError, match="可见上下文"):
        project_context(
            (
                *_messages(4),
                SessionCompactionEvent("now", "摘要", boundary, id="c1", parent_id="m3"),
            )
        )


def test_context_rejects_hidden_boundary() -> None:
    first = SessionCompactionEvent("now", "摘要", "m3", id="c1", parent_id="m3")
    second = SessionCompactionEvent("now", "重复摘要", "m1", id="c2", parent_id="c1")
    with pytest.raises(ValueError, match="可见上下文"):
        project_context((*_messages(4), first, second))


def test_context_snapshot_has_no_mutation_or_storage_capabilities() -> None:
    snapshot = project_context(_messages(1))
    assert isinstance(snapshot.nodes, tuple)
    assert isinstance(snapshot.messages, tuple)
    for name in ("append_message", "compact", "restore", "events", "session_id", "version"):
        assert not hasattr(snapshot, name)
    with pytest.raises(FrozenInstanceError):
        snapshot.leaf_id = "覆盖"
    assert ContextSnapshot().messages == ()


def test_context_keeps_summary_before_recent_messages_across_compactions() -> None:
    originals = _messages(12)
    first = SessionCompactionEvent("now", "第一次摘要", "m6", id="c1", parent_id="m7")
    events = (
        *originals[:8],
        first,
        SessionMessageEvent("now", originals[8].message, id="m8", parent_id="c1"),
        *originals[9:],
    )
    second = SessionCompactionEvent("now", "第二次摘要", "m10", id="c2", parent_id="m11")
    snapshot = project_context((*events, second))
    assert snapshot.messages == (
        UserMessage("[Compacted History]\n\n第二次摘要"),
        originals[10].message,
        originals[11].message,
    )
    assert snapshot.revision == "c2"


def test_context_follows_parent_links_not_file_order() -> None:
    root = SessionMessageEvent("now", UserMessage("共同起点"), id="root")
    branch_a_event = SessionMessageEvent("now", UserMessage("分支 A"), id="a", parent_id="root")
    branch_b_event = SessionMessageEvent("now", UserMessage("分支 B"), id="b", parent_id="root")
    events = (root, branch_a_event, branch_b_event)
    assert project_context(events).messages == (root.message, branch_b_event.message)
    assert project_context(events, "a", use_latest=False).messages == (
        root.message,
        branch_a_event.message,
    )
    assert project_context(events, None, use_latest=False).messages == ()
    with pytest.raises(ValueError, match="叶节点"):
        project_context(events, "missing", use_latest=False)


@pytest.mark.parametrize(
    "invalid",
    [
        SessionMessageEvent("now", UserMessage("重复 ID"), id="m0"),
        SessionMessageEvent("now", UserMessage("缺失父节点"), id="bad", parent_id="missing"),
        SessionMessageEvent("now", UserMessage("自引用"), id="bad", parent_id="bad"),
    ],
)
def test_tree_rejects_duplicate_and_broken_parent_links(invalid: SessionMessageEvent) -> None:
    with pytest.raises(ValueError):
        project_context((*_messages(1), invalid))


def test_compaction_on_one_branch_never_changes_sibling_context() -> None:
    originals = _messages(4)
    first = SessionCompactionEvent("now", "分支摘要", "m3", id="c1", parent_id="m3")
    sibling = SessionMessageEvent("now", UserMessage("另一个方案"), id="sibling", parent_id="m1")
    events = (*originals, first, sibling)
    assert project_context(events).messages == (
        originals[0].message,
        originals[1].message,
        sibling.message,
    )
    assert project_context(events, "c1", use_latest=False).messages == (
        UserMessage("[Compacted History]\n\n分支摘要"),
        originals[3].message,
    )


@pytest.mark.parametrize(
    "event",
    [
        SessionHeaderEvent(
            "created", "session", "/workspace", "名称", "model", False, "parent", "call"
        ),
        SessionMessageEvent("now", UserMessage("消息"), id="m1", parent_id="m0"),
        SessionMessageEvent(
            "now",
            AssistantMessage(
                (TextBlock("回答"),),
                LlmStopReason.STOP,
                provider="anthropic",
                model="model",
                usage=LlmUsage(5, 3),
            ),
        ),
        SessionCompactionEvent("now", "摘要", "m1", 100, LlmUsage(10, 5), id="c1", parent_id="m2"),
        SessionRunEvent(
            "finished",
            RunSummary(
                run_id="run-1",
                started_at="started",
                finished_at="finished",
                status="completed",
                stop_reason="model_completed",
                provider="anthropic",
                model="model",
                total_tokens=42,
                input_tokens=30,
                output_tokens=12,
                files=("src/app.py",),
                approvals=(ApprovalDecision("req-1", "write_file", True, "decided"),),
                subagent_ids=("sub-1",),
                tool_failures=1,
            ),
            id="r1",
            parent_id="m2",
        ),
        SessionInfoEvent("updated", "新名称", "/workspace", "new-model", True),
    ],
)
def test_session_events_roundtrip_tree_jsonl_shape(event: SessionEvent) -> None:
    payload = event_to_dict(event)
    assert event_from_dict(payload) == event
    assert not {"data", "seq", "start", "end"} & payload.keys()
    if isinstance(event, SessionHeaderEvent):
        assert payload["cwd"] == event.workspace
        assert payload["parentSessionId"] == "parent"
        assert payload["version"] == 1
        assert "parentId" not in payload
    else:
        assert payload["id"] == event.id
        assert payload["parentId"] == event.parent_id
    if isinstance(event, SessionCompactionEvent):
        assert payload["firstKeptEntryId"] == "m1"
        assert payload["tokensBefore"] == 100
        assert payload["summary"] == "摘要"
    if isinstance(event, SessionRunEvent):
        assert payload["summary"]["run_id"] == "run-1"
        assert payload["summary"]["approvals"] == [
            {
                "request_id": "req-1",
                "tool_name": "write_file",
                "approved": True,
                "decided_at": "decided",
            }
        ]


def test_session_events_are_distinct_from_runtime_events() -> None:
    variants = get_args(SessionEvent)
    assert len(variants) == 5
    assert {cls.__dataclass_fields__["type"].default for cls in variants} == {
        "session",
        "message",
        "compaction",
        "run",
        "session_info",
    }
    for cls in variants:
        assert not cls.__dataclass_fields__["type"].init
        assert not {"data", "run_id", "turn_id", "sequence"} & {item.name for item in fields(cls)}


def test_metadata_updates_do_not_change_context_revision() -> None:
    header = SessionHeaderEvent("created", "session", "/workspace", "初始名称")
    message = SessionMessageEvent("answered", UserMessage("消息"), id="m1")
    update = SessionInfoEvent(
        "updated", "新名称", "/other", "model", True, id="info", parent_id="m1"
    )
    events = (header, message, update)
    snapshot = project_context(events)
    assert snapshot.revision == "m1"
    assert snapshot.leaf_id == "info"
    metadata = project_metadata(events)
    assert metadata.name == "新名称"
    assert metadata.workspace == "/other"
    assert metadata.model_id == "model"
    assert metadata.archived
    assert metadata.created_at == "created"
    assert metadata.updated_at == "updated"
    assert metadata.message_count == 1
