from pathlib import Path
from typing import Any, Self

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import Field, model_validator

from camel_harness.agent.subagent.tool import DelegateTaskArguments
from camel_harness.agent.tools import (
    ApprovalHandler,
    PermissionMode,
    PermissionRequest,
    PermissionService,
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolNotExecutedError,
    ToolRegistry,
    ToolResult,
    ToolRisk,
)
from camel_harness.agent.tools.filesystem import (
    EditFileArguments,
    ReadFileArguments,
    WriteFileArguments,
)
from camel_harness.agent.tools.memory_tools import MemoryArguments
from camel_harness.agent.tools.parameters import PydanticTool, ToolArguments
from camel_harness.agent.tools.permissions import ApprovalPendingListener
from camel_harness.agent.tools.shell import RunShellArguments
from camel_harness.agent.tools.skill_loader import LoadSkillArguments


@pytest.mark.parametrize(
    ("arguments_model", "arguments", "field"),
    [
        (ReadFileArguments, {}, "path"),
        (ReadFileArguments, {"path": 12}, "path"),
        (ReadFileArguments, {"path": "file", "start_line": "2"}, "start_line"),
        (ReadFileArguments, {"path": "file", "start_line": True}, "start_line"),
        (ReadFileArguments, {"path": "file", "start_line": 1.0}, "start_line"),
        (ReadFileArguments, {"path": "file", "max_lines": 2001}, "max_lines"),
        (ReadFileArguments, {"path": "file", "max_lines": 0}, "max_lines"),
        (WriteFileArguments, {"path": "file", "content": 12}, "content"),
        (WriteFileArguments, {"path": "file", "content": "", "extra": "秘密"}, "extra"),
        (EditFileArguments, {"path": "file", "edits": []}, "edits"),
        (
            EditFileArguments,
            {"path": "file", "edits": [{"old_text": "", "new_text": "new"}]},
            "edits.0.old_text",
        ),
        (
            EditFileArguments,
            {"path": "file", "edits": [{"old_text": "old", "new_text": 12}]},
            "edits.0.new_text",
        ),
        (
            EditFileArguments,
            {"path": "file", "edits": [{"old_text": "old", "new_text": "", "extra": 1}]},
            "edits.0.extra",
        ),
        (RunShellArguments, {"command": "echo ok", "timeout_seconds": "2"}, "timeout_seconds"),
        (RunShellArguments, {"command": "echo ok", "timeout_seconds": True}, "timeout_seconds"),
        (
            RunShellArguments,
            {"command": "echo ok", "timeout_seconds": float("nan")},
            "timeout_seconds",
        ),
        (
            RunShellArguments,
            {"command": "echo ok", "timeout_seconds": float("inf")},
            "timeout_seconds",
        ),
        (RunShellArguments, {"command": "echo ok", "timeout_seconds": 121}, "timeout_seconds"),
        (LoadSkillArguments, {"name": "skill", "path": None}, "path"),
        (MemoryArguments, {"action": "add", "target": "memory"}, "content"),
        (MemoryArguments, {"action": "replace", "target": "user", "content": "new"}, "old_text"),
        (MemoryArguments, {"action": "remove", "target": "user"}, "old_text"),
        (MemoryArguments, {"action": "remove", "target": "user", "old_text": None}, "old_text"),
        (DelegateTaskArguments, {"task": "任务", "agent_type": "unknown"}, "agent_type"),
    ],
)
async def test_invalid_parameters_never_reach_approval_or_execution(
    tmp_path: Path,
    arguments_model: type[ToolArguments],
    arguments: dict[str, Any],
    field: str,
) -> None:
    class RejectApproval(ApprovalHandler):
        async def approve(
            self,
            permission_request: PermissionRequest,
            reason: str,
            on_pending: ApprovalPendingListener | None = None,
        ) -> bool:
            pytest.fail("无效参数不应触发审批")

    class RejectExecution(PydanticTool[ToolArguments]):
        name = "reject"
        risk = ToolRisk.WRITE

        async def execute_validated(
            self, arguments: ToolArguments, tool_call: ToolCall, tool_context: ToolContext
        ) -> ToolResult:
            pytest.fail("无效参数不应执行")

    tool = RejectExecution()
    tool.arguments_model = arguments_model
    tool_registry = ToolRegistry()
    tool_registry.register(tool)
    result = await ToolExecutor(tool_registry, PermissionService(RejectApproval())).execute(
        ToolCall("invalid", tool.name, arguments), ToolContext("session", tmp_path)
    )
    assert result.metadata["execution_status"] == "not_executed"
    assert field in result.content
    assert "秘密" not in result.content


async def test_parsed_parameters_are_shared_by_approval_and_execution_once(tmp_path: Path) -> None:
    original_arguments = {"path": "file", "edits": [{"old_text": "old", "new_text": "new"}]}
    parsed_arguments: list[EditFileArguments] = []
    validation_count = 0

    class RecordingArguments(EditFileArguments):
        limit: int = Field(default=5, ge=1)

        @model_validator(mode="after")
        def record_validation(self) -> Self:
            nonlocal validation_count
            validation_count += 1
            return self

    class RecordingTool(PydanticTool[RecordingArguments]):
        name = "record"
        risk = ToolRisk.WRITE
        arguments_model = RecordingArguments

        def prepare_approval_context(
            self, arguments: RecordingArguments, tool_call: ToolCall, tool_context: ToolContext
        ) -> dict[str, str]:
            parsed_arguments.append(arguments)
            return {"path": arguments.path}

        async def execute_validated(
            self, arguments: RecordingArguments, tool_call: ToolCall, tool_context: ToolContext
        ) -> ToolResult:
            assert arguments is parsed_arguments[0]
            assert arguments.path == "file"
            assert arguments.edits[0].new_text == "new"
            assert arguments.limit == 5
            return ToolResult(tool_call.call_id, tool_call.name, "ok")

    class MutatingApproval(ApprovalHandler):
        async def approve(
            self,
            permission_request: PermissionRequest,
            reason: str,
            on_pending: ApprovalPendingListener | None = None,
        ) -> bool:
            assert permission_request.arguments["limit"] == 5
            assert permission_request.execution_context == {"path": "file"}
            original_arguments["edits"][0]["new_text"] = "修改原始参数"
            permission_request.arguments["edits"][0]["new_text"] = "修改审批快照"
            return True

    tool_registry = ToolRegistry()
    tool_registry.register(RecordingTool())
    result = await ToolExecutor(tool_registry, PermissionService(MutatingApproval())).execute(
        ToolCall("record", "record", original_arguments), ToolContext("session", tmp_path)
    )
    assert result.content == "ok"
    assert result.metadata["execution_status"] == "succeeded"
    assert validation_count == 1


@pytest.mark.parametrize(
    ("arguments_model", "arguments"),
    [
        (ReadFileArguments, {"path": "file"}),
        (WriteFileArguments, {"path": "file", "content": ""}),
        (EditFileArguments, {"path": "file", "edits": [{"old_text": "old", "new_text": ""}]}),
        (RunShellArguments, {"command": "echo ok", "timeout_seconds": 120}),
        (LoadSkillArguments, {"name": "skill"}),
        (MemoryArguments, {"action": "add", "target": "memory", "content": "记忆"}),
        (DelegateTaskArguments, {"task": "任务", "agent_type": "explore"}),
    ],
)
def test_generated_schema_and_parsed_arguments_accept_valid_calls(
    arguments_model: type[ToolArguments], arguments: dict[str, Any]
) -> None:
    schema = arguments_model.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(arguments)
    parsed = arguments_model.model_validate(arguments)
    Draft202012Validator(schema).validate(parsed.model_dump(mode="json", exclude_none=True))
    if arguments_model is EditFileArguments:
        assert schema["$defs"]["TextEditArguments"]["additionalProperties"] is False
    if arguments_model is LoadSkillArguments:
        assert schema["properties"]["path"]["type"] == "string"
        assert "default" not in schema["properties"]["path"]
        with pytest.raises(JsonSchemaValidationError):
            Draft202012Validator(schema).validate({**arguments, "path": None})


async def test_external_schema_keeps_refs_composition_and_patterns(tmp_path: Path) -> None:
    class ExternalTool(Tool):
        name = "external"
        parameters = {
            "type": "object",
            "$defs": {"name": {"type": "string", "pattern": "^[a-z]+$"}},
            "properties": {"value": {"oneOf": [{"$ref": "#/$defs/name"}, {"type": "integer"}]}},
            "required": ["value"],
            "additionalProperties": False,
        }

        async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
            return ToolResult(tool_call.call_id, tool_call.name, "ok")

    tool_registry = ToolRegistry()
    tool_registry.register(ExternalTool())
    tool_executor = ToolExecutor(tool_registry)
    for value in ("lowercase", 3):
        result = await tool_executor.execute(
            ToolCall("valid", "external", {"value": value}), ToolContext("session", tmp_path)
        )
        assert result.content == "ok"
    for value in ("UPPERCASE", True, []):
        result = await tool_executor.execute(
            ToolCall("invalid", "external", {"value": value}), ToolContext("session", tmp_path)
        )
        assert result.metadata["execution_status"] == "not_executed"
    with pytest.raises(ToolNotExecutedError):
        ExternalTool().prepare_call(
            ToolCall("missing", "external", {}),
            ToolContext("session", tmp_path, PermissionMode.YOLO),
        )
