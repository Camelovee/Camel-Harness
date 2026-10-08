import json
from collections.abc import AsyncIterator

import pytest

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionCompactionEvent, SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry, validate_tool_arguments
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmUsage,
    MessageCompleted,
    TextBlock,
    TextDelta,
    UserMessage,
)


async def test_branch_append_preserves_siblings_and_restores_last_written_leaf(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("tree", tmp_path, "分支测试")
    session = manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    a = await session.append_message(UserMessage("方案 A"))
    branch_snapshot = await session.branch(root)
    assert branch_snapshot.leaf_id == root
    assert branch_snapshot.messages == (UserMessage("共同起点"),)
    b = await session.append_message(UserMessage("方案 B"))
    entries = await session.entries()
    assert [(item.id, item.parent_id) for item in entries] == [(root, None), (a, root), (b, root)]
    assert await session.messages() == (UserMessage("共同起点"), UserMessage("方案 B"))
    assert await session.context_messages() == await session.messages()
    restarted = SessionManager(JsonlSessionStorage(tmp_path)).bind("tree")
    assert (await restarted.context_snapshot()).leaf_id == b
    assert await restarted.messages() == await session.messages()
    await restarted.branch(a)
    assert await restarted.messages() == (UserMessage("共同起点"), UserMessage("方案 A"))
    # 只选择叶节点不重写文件；未追加前重启仍恢复最后写入的条目，和 pi 相同。
    assert (await manager.bind("tree").context_snapshot()).leaf_id == b
    metadata = await manager.get("tree")
    assert metadata is not None and metadata.message_count == 3


async def test_empty_root_and_invalid_branch_selection(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("tree", tmp_path, "根分支")
    session = manager.bind("tree")
    old_id = await session.append_message(UserMessage("旧根"))
    before = (manager.directory("tree") / "session.jsonl").read_bytes()
    with pytest.raises(ValueError, match="叶节点"):
        await session.branch("missing")
    assert (await session.context_snapshot()).leaf_id == old_id
    assert (manager.directory("tree") / "session.jsonl").read_bytes() == before
    assert (await session.branch(None)).messages == ()
    new_id = await session.append_message(UserMessage("新根"))
    assert await session.messages() == (UserMessage("新根"),)
    assert [(item.id, item.parent_id) for item in await session.entries()] == [
        (old_id, None),
        (new_id, None),
    ]


async def test_compaction_and_append_stay_on_selected_branch(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("tree", tmp_path, "分支压缩")
    session = manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    a = await session.append_message(UserMessage("A 的近期消息"))
    await session.branch(root)
    b = await session.append_message(UserMessage("B 的近期消息"))
    snapshot_a = await session.branch(a)
    updated = await session.compact(
        "A 摘要", a, snapshot=snapshot_a, tokens_before=100, usage=LlmUsage(12, 4)
    )
    compacted = (await session.entries())[-1]
    assert isinstance(compacted, SessionCompactionEvent)
    assert compacted.parent_id == a
    assert compacted.first_kept_entry_id == a
    assert compacted.tokens_before == 100
    assert compacted.usage == LlmUsage(12, 4)
    assert updated.messages == (
        UserMessage("[Compacted History]\n\nA 摘要"),
        UserMessage("A 的近期消息"),
    )
    following = await session.append_message(UserMessage("A 继续"))
    assert (await session.entries())[-1].parent_id == compacted.id
    restarted = manager.bind("tree")
    assert (await restarted.context_snapshot()).leaf_id == following
    assert await restarted.context_messages() == (*updated.messages, UserMessage("A 继续"))
    await restarted.branch(b)
    assert await restarted.context_messages() == (
        UserMessage("共同起点"),
        UserMessage("B 的近期消息"),
    )


async def test_changed_branch_rejects_old_compaction_plan_without_writing(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("tree", tmp_path, "分支切换")
    session = manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    a = await session.append_message(UserMessage("A"))
    await session.branch(root)
    b = await session.append_message(UserMessage("B"))
    snapshot_a = await session.branch(a)
    await session.branch(b)
    path = manager.directory("tree") / "session.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="并发修改"):
        await session.compact("A 摘要", a, snapshot=snapshot_a)
    assert path.read_bytes() == before


@pytest.mark.parametrize("defect", ["duplicate", "missing_parent", "self_parent", "old_version"])
async def test_invalid_tree_is_rejected_and_list_isolated(tmp_path, defect) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("bad", tmp_path, "坏日志")
    await manager.create("good", tmp_path, "正常日志")
    session = manager.bind("bad")
    first = await session.append_message(UserMessage("消息"))
    path = manager.directory("bad") / "session.jsonl"
    if defect == "old_version":
        rows = path.read_text("utf-8").splitlines()
        header = json.loads(rows[0])
        header["version"] = 2
        path.write_text(json.dumps(header) + "\n" + "\n".join(rows[1:]) + "\n", encoding="utf-8")
    else:
        entry_id = first if defect == "duplicate" else "invalid"
        parent_id = entry_id if defect == "self_parent" else "missing"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": entry_id,
                        "parentId": parent_id,
                        "timestamp": "now",
                        "message": {"role": "user", "content": "坏记录"},
                    }
                )
                + "\n"
            )
    with pytest.raises(ValueError, match="会话日志损坏"):
        await session.messages()
    assert [item.session_id for item in await manager.list()] == ["good"]


class RecordingLlm(LlmAdapter):
    """只记录实际模型输入，不调用真实模型。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield TextDelta("新分支回答")
        yield MessageCompleted(LlmStopReason.STOP)


async def test_agent_uses_selected_branch_not_sibling_history(tmp_path) -> None:
    manager = SessionManager(JsonlSessionStorage(tmp_path))
    await manager.create("tree", tmp_path, "Agent 分支")
    session = manager.bind("tree")
    root = await session.append_message(UserMessage("共同问题"))
    await session.append_message(AssistantMessage((TextBlock("旧分支回答"),), LlmStopReason.STOP))
    await session.branch(root)
    adapter = RecordingLlm()
    llm = LlmClient(adapter)
    registry = ToolRegistry()
    agent = Agent(
        session,
        tmp_path,
        AgentOptions("fake"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        ContextManager(CompactionEngine(llm)),
    )
    await agent.run("走新方案")
    assert adapter.requests[0].messages == (UserMessage("共同问题"), UserMessage("走新方案"))
    entries = await session.entries()
    assert entries[-2].parent_id == root
    assert entries[-1].parent_id == entries[-2].id
    assert len(entries) == 4
    assert len(await session.messages()) == 3
