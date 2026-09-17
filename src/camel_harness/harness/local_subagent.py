from __future__ import annotations

from dataclasses import replace

from camel_harness import (
    Agent,
    AgentLoop,
    AgentOptions,
    AgentRun,
    AgentStreamEventType,
    SubagentRequest,
    SubagentResult,
    SubagentType,
)
from camel_harness.harness.compaction import ContextManager
from camel_harness.harness.session import SessionManager
from camel_harness.llm import LlmClient
from camel_harness.tools import PermissionService, ToolExecutor, ToolRegistry
from camel_harness.types import Message, MessageRole, PermissionMode, ToolArgumentValidator
from camel_harness.types.identifiers import SessionId

_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "grep_search",
        "skill_list",
        "skill_load",
        "memory_list",
        "memory_load",
    }
)
_GENERAL_TOOLS = _READ_ONLY_TOOLS | frozenset({"write_file", "edit_file", "run_shell"})


class LocalSubagentRunner:
    """复用 Agent Loop，但使用隔离会话、预算和工具目录。"""

    def __init__(
        self,
        llm: LlmClient,
        sessions: SessionManager,
        registry: ToolRegistry,
        validate_arguments: ToolArgumentValidator,
        permission: PermissionService,
        context: ContextManager,
        options: AgentOptions,
    ) -> None:
        self._llm = llm
        self._sessions = sessions
        self._registry = registry
        self._validate_arguments = validate_arguments
        self._permission = permission
        self._context = context
        self._options = options

    async def run(self, request: SubagentRequest) -> SubagentResult:
        allowed = _GENERAL_TOOLS if request.agent_type is SubagentType.GENERAL else _READ_ONLY_TOOLS
        registry = self._registry.subset(allowed)
        executor = ToolExecutor(registry, self._validate_arguments, self._permission)
        options = replace(self._options, max_steps=8)
        agent = Agent(options, AgentLoop(self._llm, registry, executor))
        session_id = f"sub-{SessionId.new().value}"
        mode = (
            request.permission_mode
            if request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )
        prompt = f"你是 {request.agent_type.value} 子 Agent。只完成被委派任务并返回精炼结果。"
        await self._sessions.ensure(session_id, request.workspace, "子 Agent")
        await self._sessions.append_message(
            session_id, Message.text(MessageRole.USER, request.task)
        )
        dynamic_context = await self._context.build(request.task, request.workspace)
        if dynamic_context:
            prompt = f"{prompt}\n\n{dynamic_context}"

        async def load_context(run: AgentRun, active_options: AgentOptions) -> tuple[Message, ...]:
            log = await self._sessions.context_log(session_id)
            compacted = await self._context.compact_if_needed(log, run.turn_id, active_options)
            if compacted is not None:
                await self._sessions.replace_context(
                    session_id,
                    compacted.start_seq,
                    compacted.end_seq,
                    compacted.summary_message,
                )
                run.record_tokens(compacted.input_tokens, compacted.output_tokens)
            return await self._sessions.context_messages(session_id)

        async def on_message(message: Message) -> None:
            await self._sessions.append_message(session_id, message)

        parts: list[str] = []
        try:
            async for event in agent.run(
                session_id=session_id,
                workspace=request.workspace,
                system_prompt=prompt,
                load_context=load_context,
                on_message=on_message,
                permission_mode=mode,
            ):
                if event.type is AgentStreamEventType.TEXT_DELTA:
                    parts.append(str(event.data.get("text", "")))
        except Exception as exc:
            return SubagentResult(session_id, f"子 Agent 执行失败：{exc}", False)
        return SubagentResult(session_id, "".join(parts), True)
