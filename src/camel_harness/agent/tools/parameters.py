"""本地工具的严格参数模型与审批前解析入口。"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import replace
from functools import partial
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .types import PreparedToolCall, Tool, ToolCall, ToolContext, ToolNotExecutedError, ToolResult


def _omit_null_defaults(schema: dict[str, Any]) -> None:
    """缺省 None 仅用于内部表示省略，不能提示模型显式发送 null。"""

    for field_schema in schema.get("properties", {}).values():
        if field_schema.get("default", ...) is None:
            field_schema.pop("default")


class ToolArguments(BaseModel):
    """禁止隐式转换、额外字段和非有限数值的工具参数。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        json_schema_extra=_omit_null_defaults,
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        """字段可以省略，但模型显式传入 null 仍是参数错误。"""

        if value is None:
            raise ValueError("参数不能为 null")
        return value


ArgumentsT = TypeVar("ArgumentsT", bound=ToolArguments)


class PydanticTool(Tool, Generic[ArgumentsT]):
    """由参数模型生成 Schema，并将一次解析结果绑定到执行入口。"""

    arguments_model: type[ArgumentsT]

    @property
    def parameters(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    def _parse_arguments(self, tool_call: ToolCall) -> ArgumentsT:
        try:
            return self.arguments_model.model_validate(tool_call.arguments)
        except ValidationError as exc:
            raise ToolNotExecutedError(_validation_message(exc)) from exc

    def prepare_call(self, tool_call: ToolCall, tool_context: ToolContext) -> PreparedToolCall:
        arguments = self._parse_arguments(tool_call)
        # 审批显示实际使用的默认值；缺省的可选文本字段不显示为 null。
        prepared_call = replace(
            tool_call, arguments=arguments.model_dump(mode="json", exclude_none=True)
        )
        return PreparedToolCall(
            arguments=prepared_call.arguments,
            execution_context=self.prepare_approval_context(arguments, prepared_call, tool_context),
            execute=partial(self.execute_validated, arguments, prepared_call, tool_context),
        )

    def prepare_approval_context(
        self, arguments: ArgumentsT, tool_call: ToolCall, tool_context: ToolContext
    ) -> dict[str, str]:
        """使用已解析参数生成审批信息，不执行操作。"""

        return {}

    def approval_context(self, tool_call: ToolCall, tool_context: ToolContext) -> dict[str, str]:
        return self.prepare_approval_context(
            self._parse_arguments(tool_call), tool_call, tool_context
        )

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        """直接调用工具时同样校验参数；执行器使用 prepare_call 绑定的入口。"""

        return await self.execute_validated(
            self._parse_arguments(tool_call), tool_call, tool_context
        )

    @abstractmethod
    async def execute_validated(
        self, arguments: ArgumentsT, tool_call: ToolCall, tool_context: ToolContext
    ) -> ToolResult:
        """使用已校验参数执行工具。"""

        raise NotImplementedError


def _validation_message(error: ValidationError) -> str:
    """只返回字段位置和约束说明，不把原始输入带入错误结果。"""

    messages: list[str] = []
    descriptions = {
        "missing": "缺少必填参数",
        "extra_forbidden": "不允许额外参数",
        "string_type": "必须是字符串",
        "int_type": "必须是整数",
        "float_type": "必须是数值",
        "list_type": "必须是数组",
        "model_type": "必须是对象",
        "literal_error": "不在允许的取值范围内",
        "finite_number": "必须是有限数值",
    }
    for detail in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"]) or "参数"
        code = detail["type"]
        context = detail.get("ctx", {})
        if code == "value_error":
            description = str(context.get("error", "参数约束不满足"))
        elif code in ("string_too_short", "string_too_long", "too_short", "too_long"):
            bound = "min_length" if code.endswith("short") else "max_length"
            description = (
                f"长度{'至少' if bound == 'min_length' else '至多'}为 {context.get(bound)}"
            )
        elif code in ("greater_than_equal", "less_than_equal"):
            bound = "ge" if code == "greater_than_equal" else "le"
            description = f"必须{'大于等于' if bound == 'ge' else '小于等于'} {context.get(bound)}"
        else:
            description = descriptions.get(code, "参数约束不满足")
        messages.append(f"{location}：{description}")
    return "工具参数校验失败：" + "；".join(messages)
