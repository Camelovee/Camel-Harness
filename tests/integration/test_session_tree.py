import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.context.token_counting import (
    estimate_context_tokens,
    estimate_message_tokens,
)
from camel_harness.agent.session import SessionCompactionEvent, SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry
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
    TextBlock,
    TextDelta,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


async def test_branch_append_preserves_siblings_and_restores_last_written_leaf(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("tree", tmp_path, "分支测试")
    session = session_manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    branch_a_entry_id = await session.append_message(UserMessage("方案 A"))
    branch_snapshot = await session.branch(root)
    assert branch_snapshot.leaf_id == root
    assert branch_snapshot.messages == (UserMessage("共同起点"),)
    branch_b_entry_id = await session.append_message(UserMessage("方案 B"))
    entries = await session.entries()
    assert [(item.id, item.parent_id) for item in entries] == [
        (root, None),
        (branch_a_entry_id, root),
        (branch_b_entry_id, root),
    ]
    assert await session.messages() == (UserMessage("共同起点"), UserMessage("方案 B"))
    assert (await session.context_snapshot()).messages == await session.messages()
    restarted = SessionManager(JsonlSessionStorage(tmp_path)).bind("tree")
    assert (await restarted.context_snapshot()).leaf_id == branch_b_entry_id
    assert await restarted.messages() == await session.messages()
    await restarted.branch(branch_a_entry_id)
    assert await restarted.messages() == (UserMessage("共同起点"), UserMessage("方案 A"))
    # 只选择叶节点不重写文件；未追加前重启仍恢复最后写入的条目，和 pi 相同。
    assert (await session_manager.bind("tree").context_snapshot()).leaf_id == branch_b_entry_id
    metadata = await session_manager.get("tree")
    assert metadata is not None and metadata.message_count == 3


async def test_empty_root_and_invalid_branch_selection(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("tree", tmp_path, "根分支")
    session = session_manager.bind("tree")
    old_id = await session.append_message(UserMessage("旧根"))
    before = (session_manager.directory("tree") / "session.jsonl").read_bytes()
    with pytest.raises(ValueError, match="叶节点"):
        await session.branch("missing")
    assert (await session.context_snapshot()).leaf_id == old_id
    assert (session_manager.directory("tree") / "session.jsonl").read_bytes() == before
    assert (await session.branch(None)).messages == ()
    new_id = await session.append_message(UserMessage("新根"))
    assert await session.messages() == (UserMessage("新根"),)
    assert [(item.id, item.parent_id) for item in await session.entries()] == [
        (old_id, None),
        (new_id, None),
    ]


async def test_compaction_and_append_stay_on_selected_branch(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("tree", tmp_path, "分支压缩")
    session = session_manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    branch_a_entry_id = await session.append_message(UserMessage("A 的近期消息"))
    await session.branch(root)
    branch_b_entry_id = await session.append_message(UserMessage("B 的近期消息"))
    branch_a_snapshot = await session.branch(branch_a_entry_id)
    updated = await session.compact(
        "A 摘要",
        branch_a_entry_id,
        snapshot=branch_a_snapshot,
        tokens_before=100,
        usage=LlmUsage(12, 4),
    )
    compacted = (await session.entries())[-1]
    assert isinstance(compacted, SessionCompactionEvent)
    assert compacted.parent_id == branch_a_entry_id
    assert compacted.first_kept_entry_id == branch_a_entry_id
    assert compacted.tokens_before == 100
    assert compacted.usage == LlmUsage(12, 4)
    assert updated.messages == (
        UserMessage("[Compacted History]\n\nA 摘要"),
        UserMessage("A 的近期消息"),
    )
    following = await session.append_message(UserMessage("A 继续"))
    assert (await session.entries())[-1].parent_id == compacted.id
    restarted = session_manager.bind("tree")
    assert (await restarted.context_snapshot()).leaf_id == following
    assert (await restarted.context_snapshot()).messages == (
        *updated.messages,
        UserMessage("A 继续"),
    )
    await restarted.branch(branch_b_entry_id)
    assert (await restarted.context_snapshot()).messages == (
        UserMessage("共同起点"),
        UserMessage("B 的近期消息"),
    )


async def test_usage_boundary_survives_compactions_branches_and_session_reload(
    tmp_path: Path,
) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("usage", tmp_path, "用量边界")
    session = session_manager.bind("usage")
    await session.append_message(UserMessage("较早任务"))
    kept_entry_id = await session.append_message(UserMessage("近期任务"))
    old_answer = AssistantMessage(
        (TextBlock("近期回答"),),
        LlmStopReason.STOP,
        provider=_MODEL.provider,
        model=_MODEL.model,
        usage=LlmUsage(5000, 100),
    )
    old_answer_id = await session.append_message(old_answer)
    snapshot = await session.context_snapshot()
    assert snapshot.usage_start_index == 0
    assert estimate_context_tokens(snapshot, _MODEL).total_tokens == 5100
    snapshot = await session.compact(
        "第一次摘要", kept_entry_id, snapshot=snapshot, usage=LlmUsage(9000, 500)
    )
    first_compaction_id = snapshot.leaf_id
    assert snapshot.usage_start_index == len(snapshot.nodes) == 3
    assert estimate_context_tokens(snapshot, _MODEL).last_usage_index is None

    # 重新读取日志时应重建边界，摘要请求的 usage 也不能成为任务请求的基数。
    restarted_session = SessionManager(JsonlSessionStorage(tmp_path)).bind("usage")
    reloaded_snapshot = await restarted_session.context_snapshot()
    assert reloaded_snapshot == snapshot
    estimate = estimate_context_tokens(reloaded_snapshot, _MODEL, context_overhead_tokens=50)
    assert estimate.total_tokens == 50 + sum(
        estimate_message_tokens(message) for message in snapshot.messages
    )
    assert estimate.usage_tokens == 0

    # 切回未压缩分支时可继续使用该分支的 usage，不受其他分支的压缩影响。
    old_snapshot = await restarted_session.branch(old_answer_id)
    assert old_snapshot.usage_start_index == 0
    assert estimate_context_tokens(old_snapshot, _MODEL).total_tokens == 5100
    await restarted_session.branch(first_compaction_id)
    following_id = await restarted_session.append_message(UserMessage("压缩后继续"))
    assert (
        estimate_context_tokens(await restarted_session.context_snapshot(), _MODEL).last_usage_index
        is None
    )
    new_answer = AssistantMessage(
        (TextBlock("新回答"),),
        LlmStopReason.STOP,
        provider=_MODEL.provider,
        model=_MODEL.model,
        usage=LlmUsage(300, 30),
    )
    await restarted_session.append_message(new_answer)
    snapshot = await restarted_session.context_snapshot()
    assert estimate_context_tokens(snapshot, _MODEL).total_tokens == 330
    assert estimate_context_tokens(snapshot, _MODEL).last_usage_index == 4

    snapshot = await restarted_session.compact("第二次摘要", following_id, snapshot=snapshot)
    assert snapshot.usage_start_index == len(snapshot.nodes) == 3
    assert estimate_context_tokens(snapshot, _MODEL).last_usage_index is None
    await restarted_session.append_message(new_answer)
    snapshot = await SessionManager(JsonlSessionStorage(tmp_path)).bind("usage").context_snapshot()
    assert snapshot.usage_start_index == 3
    assert estimate_context_tokens(snapshot, _MODEL).last_usage_index == 3
    assert estimate_context_tokens(snapshot, _MODEL).total_tokens == 330
    payload = (session_manager.directory("usage") / "session.jsonl").read_text("utf-8")
    assert "usage_start_index" not in payload and "usageStartIndex" not in payload


async def test_changed_branch_rejects_old_compaction_plan_without_writing(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("tree", tmp_path, "分支切换")
    session = session_manager.bind("tree")
    root = await session.append_message(UserMessage("共同起点"))
    branch_a_entry_id = await session.append_message(UserMessage("A"))
    await session.branch(root)
    branch_b_entry_id = await session.append_message(UserMessage("B"))
    branch_a_snapshot = await session.branch(branch_a_entry_id)
    await session.branch(branch_b_entry_id)
    path = session_manager.directory("tree") / "session.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="并发修改"):
        await session.compact("A 摘要", branch_a_entry_id, snapshot=branch_a_snapshot)
    assert path.read_bytes() == before


@pytest.mark.parametrize("defect", ["duplicate", "missing_parent", "self_parent", "old_version"])
async def test_invalid_tree_is_rejected_and_list_isolated(tmp_path, defect) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("bad", tmp_path, "坏日志")
    await session_manager.create("good", tmp_path, "正常日志")
    session = session_manager.bind("bad")
    first = await session.append_message(UserMessage("消息"))
    path = session_manager.directory("bad") / "session.jsonl"
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
    assert [item.session_id for item in await session_manager.list()] == ["good"]


class RecordingLlm(LlmAdapter):
    """只记录实际模型输入，不调用真实模型。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(llm_request)
        yield TextDelta("新分支回答")
        yield MessageCompleted(LlmStopReason.STOP)


async def test_agent_uses_selected_branch_not_sibling_history(tmp_path) -> None:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path))
    await session_manager.create("tree", tmp_path, "Agent 分支")
    session = session_manager.bind("tree")
    root = await session.append_message(UserMessage("共同问题"))
    await session.append_message(AssistantMessage((TextBlock("旧分支回答"),), LlmStopReason.STOP))
    await session.branch(root)
    adapter = RecordingLlm()
    llm_client = LlmClient(adapter)
    tool_registry = ToolRegistry()
    agent = Agent(
        session=session,
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    await agent.run("走新方案", model=_MODEL)
    assert adapter.requests[0].messages == (UserMessage("共同问题"), UserMessage("走新方案"))
    entries = await session.entries()
    assert entries[-2].parent_id == root
    assert entries[-1].parent_id == entries[-2].id
    assert len(entries) == 4
    assert len(await session.messages()) == 3
