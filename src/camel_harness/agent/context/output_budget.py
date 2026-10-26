from __future__ import annotations

from camel_harness.llm import LlmModel

# 估算误差缓冲，与 CompactionSettings.reserve_tokens 的业务预留是两回事。
CONTEXT_SAFETY_TOKENS = 4096


class ContextBudgetError(RuntimeError):
    """估算上下文已没有可安全请求的输出空间。"""


def resolve_output_budget(model: LlmModel, input_tokens: int, maximum: int | None = None) -> int:
    """限制单次输出预算；估算有误差，不能代替服务端容量检查。"""
    available = model.context_window_tokens - input_tokens - CONTEXT_SAFETY_TOKENS
    if available <= 0:
        raise ContextBudgetError("上下文容量不足：扣除 4096 Token 安全余量后没有输出空间")
    return min(model.max_output_tokens, maximum or model.max_output_tokens, available)


def context_input_limit(model: LlmModel, reserve_tokens: int) -> int:
    """压缩策略预留与模型输出预留取较大值。"""
    # 取 max：普通回答的输出空间不能被压缩预留挤掉。
    return model.context_window_tokens - max(
        reserve_tokens, model.max_output_tokens + CONTEXT_SAFETY_TOKENS
    )
