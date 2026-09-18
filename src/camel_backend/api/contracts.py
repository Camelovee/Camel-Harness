from typing import Literal

from pydantic import BaseModel, Field


class SessionCreateBody(BaseModel):
    """创建会话的 HTTP 请求体。"""

    name: str = Field(default="新会话", min_length=1, max_length=120)
    workspace: str | None = Field(default=None, max_length=2_000)


class SessionUpdateBody(BaseModel):
    """更新会话元数据的 HTTP 请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class TurnBody(BaseModel):
    """浏览器发起 Agent Turn 的 HTTP 请求体。"""

    prompt: str = Field(min_length=1, max_length=100_000)
    permission_mode: Literal["default", "accept_edits", "plan", "yolo"] = "default"
    max_steps: int = Field(default=20, ge=1, le=100)
    thinking: bool = False


class ResolveApprovalBody(BaseModel):
    """浏览器提交审批决定的 HTTP 请求体。"""

    approved: bool
