import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.config import MemorySettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.context.system_prompt import (
    DEFAULT_SYSTEM_PROMPT,
    SUBAGENT_SYSTEM_PROMPTS,
)
from camel_harness.agent.resources import MemoryEntry, MemoryStore, ProjectRules, Skills
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.subagent import (
    LocalSubagentRunner,
    SubagentRequest,
    SubagentResult,
    SubagentType,
)
from camel_harness.agent.tools import PermissionMode, PermissionService, ToolExecutor, ToolRegistry
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    StreamFailed,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


@pytest.mark.parametrize("memory_enabled", [False, True])
async def test_concurrent_roles_keep_independent_prompts_and_shared_resources(
    tmp_path: Path, memory_enabled: bool
) -> None:
    rules = "共享项目规则"
    (tmp_path / "AGENTS.md").write_text(rules + "\n不应注入额外规则", encoding="utf-8")
    skill_directory = tmp_path / ".camel" / "skills" / "shared-context"
    skill_directory.mkdir(parents=True)
    (skill_directory / "SKILL.md").write_text(
        "---\nname: shared-context\ndescription: 共享技能索引\n---\n技能正文按需加载\n",
        encoding="utf-8",
    )
    memory_store = MemoryStore(tmp_path, tmp_path / "user-memory")
    for index in range(2):
        await memory_store.save(
            MemoryEntry(f"language-{index}", "中文注释", f"记忆正文{index}"),
            expected_revision="0",
            session_id="seed",
            tool_call_id="seed",
        )

    class ConcurrentRoleLlm(LlmAdapter):
        """等待所有角色同时进入请求，暴露共享提示被修改造成的串扰。"""

        def __init__(self) -> None:
            self.requests_by_task: dict[str, LlmRequest] = {}
            self.all_started = asyncio.Event()

        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            user_message = llm_request.messages[-1]
            assert isinstance(user_message, UserMessage)
            self.requests_by_task[user_message.content] = llm_request
            if len(self.requests_by_task) == len(SubagentType) + 1:
                self.all_started.set()
            await self.all_started.wait()
            yield TextDelta("角色任务完成")
            yield MessageCompleted(LlmStopReason.STOP)

    adapter = ConcurrentRoleLlm()
    llm_client = LlmClient(adapter)
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(llm_client=llm_client),
        system_prompt="父 Agent 基础提示",
        project_rules=ProjectRules(),
        skills=Skills(tmp_path, user_root=tmp_path / "user-skills"),
        memory_store=memory_store,
        max_rules_chars=len(rules),
        memory_settings=MemorySettings(enabled=memory_enabled, max_entries=1, context_tokens=600),
    )
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    tool_registry = ToolRegistry()
    permission_service = PermissionService()
    parent_agent = Agent(
        session=session_manager.bind("parent"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(
            tool_registry=tool_registry, permission_service=permission_service
        ),
        context_manager=context_manager,
    )
    subagent_runner = LocalSubagentRunner(
        llm_client=llm_client,
        session_manager=session_manager,
        tool_registry=tool_registry,
        permission_service=permission_service,
        context_manager=context_manager,
        agent_options=AgentOptions(),
    )
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                parent_agent.run("中文注释父任务", model=_MODEL),
                *(
                    subagent_runner.run(
                        SubagentRequest(
                            task=f"中文注释{agent_type.value}",
                            agent_type=agent_type,
                            workspace=tmp_path,
                            permission_mode=PermissionMode.DEFAULT,
                            parent_session_id="parent",
                            parent_tool_call_id=f"delegate-{agent_type.value}",
                            model=_MODEL,
                        )
                    )
                    for agent_type in SubagentType
                ),
            ),
            timeout=10,
        )
        for subagent_result in results[1:]:
            assert isinstance(subagent_result, SubagentResult)
            assert subagent_result.succeeded
        await parent_agent.run("中文注释复查", model=_MODEL)
    finally:
        await parent_agent.close()

    expected_prompts_by_task = {
        "中文注释父任务": "父 Agent 基础提示",
        "中文注释复查": "父 Agent 基础提示",
        **{
            f"中文注释{agent_type.value}": SUBAGENT_SYSTEM_PROMPTS[agent_type.value]
            for agent_type in SubagentType
        },
    }
    assert set(SUBAGENT_SYSTEM_PROMPTS) == {agent_type.value for agent_type in SubagentType}
    assert len(set(SUBAGENT_SYSTEM_PROMPTS.values())) == len(SubagentType)
    for agent_type in SubagentType:
        system_prompt = adapter.requests_by_task[f"中文注释{agent_type.value}"].system_prompt
        assert system_prompt is not None
        assert system_prompt.startswith(f"你是 {agent_type.value} 子 Agent。")
        assert "父 Agent 基础提示" not in system_prompt

    resource_sections = set()
    for task, llm_request in adapter.requests_by_task.items():
        system_prompt = llm_request.system_prompt
        assert system_prompt is not None
        base_prompt = expected_prompts_by_task[task]
        assert system_prompt.startswith(base_prompt + "\n\n")
        assert system_prompt.count(base_prompt) == 1
        assert DEFAULT_SYSTEM_PROMPT not in system_prompt
        assert rules in system_prompt and "不应注入额外规则" not in system_prompt
        assert "shared-context (project)" in system_prompt and "共享技能索引" in system_prompt
        assert "技能正文按需加载" not in system_prompt
        assert system_prompt.count("记忆正文") == int(memory_enabled)
        resource_sections.add(system_prompt.removeprefix(base_prompt + "\n\n"))
    assert len(resource_sections) == 1


class ChildLlm(LlmAdapter):
    """可阻塞或失败的子模型，用于确认委派任务清理边界。"""

    def __init__(self, blocking: bool) -> None:
        self.blocking = blocking
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    @property
    def provider_id(self):
        return "fake"

    async def stream(self, llm_request):
        try:
            yield TextDelta("未完成的回答")
            self.started.set()
            if self.blocking:
                await asyncio.Event().wait()
            else:
                yield StreamFailed("模型失败")
        finally:
            self.closed.set()


async def make_runner(tmp_path, adapter):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    llm_client = LlmClient(adapter)
    subagent_runner = LocalSubagentRunner(
        llm_client=llm_client,
        session_manager=session_manager,
        tool_registry=ToolRegistry(),
        permission_service=PermissionService(),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
        agent_options=AgentOptions(),
    )
    subagent_request = SubagentRequest(
        "子任务",
        SubagentType.EXPLORE,
        tmp_path,
        PermissionMode.DEFAULT,
        "parent",
        "delegate",
        _MODEL,
    )
    return subagent_runner, subagent_request, session_manager


async def test_child_failed_terminal_is_not_success_or_partial_text(tmp_path):
    adapter = ChildLlm(False)
    subagent_runner, subagent_request, session_manager = await make_runner(tmp_path, adapter)
    result = await subagent_runner.run(subagent_request)
    assert not result.succeeded
    assert result.content == "模型失败"
    assert [
        message.role for message in await session_manager.bind(result.session_id).messages()
    ] == ["user"]
    assert adapter.closed.is_set()


async def test_parent_cancellation_closes_child_owned_task(tmp_path):
    adapter = ChildLlm(True)
    subagent_runner, subagent_request, session_manager = await make_runner(tmp_path, adapter)
    task = asyncio.create_task(subagent_runner.run(subagent_request))
    await adapter.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert adapter.closed.is_set()
    children = [item for item in await session_manager.list() if item.parent_session_id == "parent"]
    assert len(children) == 1
    assert [
        message.role for message in await session_manager.bind(children[0].session_id).messages()
    ] == ["user"]


async def test_child_reads_its_own_archived_result(tmp_path):
    from camel_harness.agent.tools.builtin import BuiltinToolProvider

    class ReadingLlm(LlmAdapter):
        requests = 0

        @property
        def provider_id(self):
            return "fake"

        async def stream(self, llm_request):
            self.requests += 1
            if self.requests == 1:
                yield ToolCallCompleted(ToolUseBlock("source", "read_file", {"path": "source.txt"}))
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            elif self.requests == 2:
                result = llm_request.messages[-1]
                assert isinstance(result, ToolResultMessage)
                path = result.details["content_ref"]["path"]
                yield ToolCallCompleted(
                    ToolUseBlock("reference", "read_file", {"path": path, "max_lines": 1})
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                result = llm_request.messages[-1]
                assert isinstance(result, ToolResultMessage) and not result.is_error
                assert "第一行" in result.content
                yield TextDelta("已读取子会话引用")
                yield MessageCompleted(LlmStopReason.STOP)

    (tmp_path / "source.txt").write_text("第一行\n" + "内容" * 15000, encoding="utf-8")
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    adapter = ReadingLlm()
    llm_client = LlmClient(adapter)
    subagent_runner = LocalSubagentRunner(
        llm_client=llm_client,
        session_manager=session_manager,
        tool_registry=tool_registry,
        permission_service=PermissionService(),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
        agent_options=AgentOptions(),
    )
    result = await subagent_runner.run(
        SubagentRequest(
            "读取文件及引用",
            SubagentType.EXPLORE,
            tmp_path,
            PermissionMode.DEFAULT,
            "parent",
            "delegate",
            _MODEL,
        )
    )
    assert result.succeeded and result.content == "已读取子会话引用"
    assert adapter.requests == 3
    messages = await session_manager.bind(result.session_id).messages()
    references = [
        message.details["content_ref"]["path"]
        for message in messages
        if isinstance(message, ToolResultMessage) and "content_ref" in message.details
    ]
    assert references and all(
        str(session_manager.directory(result.session_id)) in path for path in references
    )
    assert await session_manager.bind("parent").messages() == ()


@pytest.mark.parametrize("kind", list(SubagentType))
async def test_child_memory_tools_are_read_only_and_do_not_close_shared_llm(tmp_path, kind):
    from camel_harness.agent.resources import MemoryEntry, MemoryStore
    from camel_harness.agent.tools.memory_tools import MemoryToolProvider

    memory_store = MemoryStore(tmp_path, tmp_path / "user-memory")
    await memory_store.save(
        MemoryEntry("language", "中文注释", "中文说明"),
        expected_revision="0",
        session_id="seed",
        tool_call_id="seed",
    )

    class MemoryLlm(LlmAdapter):
        requests = 0

        @property
        def provider_id(self):
            return "fake"

        async def stream(self, llm_request):
            self.requests += 1
            names = {tool.name for tool in llm_request.tools}
            assert {"search_memory", "read_memory"} <= names
            assert not {"save_memory", "delete_memory"} & names
            assert "中文说明" in llm_request.system_prompt
            if self.requests == 1:
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "read",
                        "read_memory",
                        {
                            "scope": "project",
                            "name": "language",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            elif self.requests == 2:
                assert "中文说明" in llm_request.messages[-1].content
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "write",
                        "save_memory",
                        {
                            "scope": "user",
                            "name": "unauthorized",
                            "description": "禁止",
                            "content": "不应写入",
                            "expected_revision": "0",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                assert llm_request.messages[-1].is_error
                yield TextDelta("仅完成只读记忆查询")
                yield MessageCompleted(LlmStopReason.STOP)

    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(MemoryToolProvider(memory_store))
    adapter = MemoryLlm()
    llm_client = LlmClient(adapter)
    subagent_runner = LocalSubagentRunner(
        llm_client=llm_client,
        session_manager=session_manager,
        tool_registry=tool_registry,
        permission_service=PermissionService(),
        context_manager=ContextManager(
            system_prompt="测试提示",
            compaction_engine=CompactionEngine(llm_client=llm_client),
            memory_store=memory_store,
        ),
        agent_options=AgentOptions(),
    )
    result = await subagent_runner.run(
        SubagentRequest(
            "查询中文注释",
            kind,
            tmp_path,
            PermissionMode.YOLO,
            "parent",
            "delegate",
            _MODEL,
        )
    )
    assert result.succeeded and result.content == "仅完成只读记忆查询"
    assert adapter.requests == 3
    assert await memory_store.get("unauthorized", "user") is None
