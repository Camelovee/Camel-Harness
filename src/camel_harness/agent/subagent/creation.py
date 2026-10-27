from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from camel_harness.llm import LlmClient

from ..agent import Agent
from ..context import ContextManager
from ..context.system_prompt import SUBAGENT_SYSTEM_PROMPTS
from ..session import SessionManager
from ..state import AgentOptions
from ..tools import PermissionService, ToolExecutor, ToolRegistry
from ..tools.file_result_storage import FileToolResultStorage
from ..tools.filesystem import ReadFileTool
from .types import SubagentRequest, SubagentType

_READ_ONLY_TOOLS = frozenset({"read_file", "load_skill"})
_GENERAL_TOOLS = _READ_ONLY_TOOLS | frozenset({"write_file", "edit_file", "run_shell"})


async def build_subagent(
    subagent_request: SubagentRequest,
    *,
    llm_client: LlmClient,
    session_manager: SessionManager,
    tool_registry: ToolRegistry,
    permission_service: PermissionService,
    context_manager: ContextManager,
    agent_options: AgentOptions,
) -> Agent:
    """组装隔离子 Agent；共享依赖均为借用，由调用方负责关闭返回的 Agent。"""

    # 显式白名单默认拒绝：排除二次委派、memory 与 MCP，避免递归和越权写入。
    allowed_tool_names = (
        _GENERAL_TOOLS if subagent_request.agent_type is SubagentType.GENERAL else _READ_ONLY_TOOLS
    )
    session_id = f"sub-{uuid4().hex}"
    system_prompt = SUBAGENT_SYSTEM_PROMPTS[subagent_request.agent_type.value]
    await session_manager.create(
        session_id,
        subagent_request.workspace,
        "子 Agent",
        parent_session_id=subagent_request.parent_session_id,
        parent_tool_call_id=subagent_request.parent_tool_call_id,
    )
    session = session_manager.bind(session_id)

    async def registered_paths(requested_id: str) -> frozenset[Path]:
        # 结果读取只认子会话自身 ID，父会话与兄弟会话都不能借用。
        if requested_id != session_id:
            raise ValueError("结果读取必须使用子 Agent 自身会话")
        return await session_manager.tool_result_paths(requested_id)

    tool_result_storage = FileToolResultStorage(
        session_manager.directory, registered_paths=registered_paths
    )
    selected_tool_registry = tool_registry.subset(allowed_tool_names)
    child_tool_registry = ToolRegistry()
    for tool_definition in selected_tool_registry.llm_definitions():
        tool = selected_tool_registry.resolve(tool_definition.name)
        # 内置读取工具绑定子会话，其他扩展工具保留注册者提供的实现。
        # 其余实例与父 Agent 共享，只有读取工具需要换会话绑定。
        child_tool_registry.register(
            ReadFileTool(tool_result_storage.resolve_read_path)
            if type(tool) is ReadFileTool
            else tool
        )
    tool_executor = ToolExecutor(
        tool_registry=child_tool_registry,
        permission_service=permission_service,
        normalize_result=tool_result_storage.normalize,
    )
    return Agent(
        session=session,
        workspace=subagent_request.workspace,
        agent_options=agent_options,
        llm_client=llm_client,
        tool_executor=tool_executor,
        context_manager=context_manager.with_system_prompt(system_prompt),
    )
