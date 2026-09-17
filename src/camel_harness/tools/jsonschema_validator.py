from __future__ import annotations

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from camel_harness.types import ToolDefinition


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: dict[str, object],
) -> tuple[str, ...]:
    """使用 Draft 2020-12 校验一组工具参数。"""

    errors = Draft202012Validator(definition.input_schema).iter_errors(arguments)
    return tuple(error.message for error in sorted(errors, key=lambda item: list(item.path)))
