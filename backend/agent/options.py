from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentOptions:
    """创建和运行 ReAct Agent 所需的稳定选项。"""

    provider: str
    model: str
    max_output_tokens: int = 4096
    max_steps: int = 20
    max_total_tokens: int = 200_000
    max_duration_seconds: float = 900
    thinking: bool = False

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("Agent Provider 不能为空")
        if not self.model.strip():
            raise ValueError("Agent Model 不能为空")
        if self.max_output_tokens <= 0:
            raise ValueError("Agent 最大输出 token 必须大于 0")
        if self.max_steps <= 0:
            raise ValueError("Agent 最大 Step 数必须大于 0")
        if self.max_total_tokens <= 0:
            raise ValueError("Agent 总 token 预算必须大于 0")
        if self.max_duration_seconds <= 0:
            raise ValueError("Agent 最大运行时间必须大于 0")
