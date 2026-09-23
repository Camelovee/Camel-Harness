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
    model_id: str | None = Field(default=None, min_length=1, max_length=64)


class RunBody(BaseModel):
    """浏览器发起一次 Agent Run 的 HTTP 请求体。"""

    prompt: str = Field(min_length=1, max_length=100_000)
    permission_mode: Literal["default", "accept_edits", "plan", "yolo"] = "default"
    max_turns: int = Field(default=20, ge=1, le=100)
    thinking: bool = False
    model_id: str | None = Field(default=None, min_length=1, max_length=64)


class ResumeBody(BaseModel):
    """浏览器恢复一个可继续 Session 的请求体。"""

    permission_mode: Literal["default", "accept_edits", "plan", "yolo"] = "default"
    max_turns: int = Field(default=20, ge=1, le=100)
    thinking: bool = False
    model_id: str | None = Field(default=None, min_length=1, max_length=64)


class PreferencesBody(BaseModel):
    """用户的 Web 默认模型与思考模式。"""

    default_model_id: str = Field(min_length=1, max_length=64)
    thinking: bool


class SteerBody(BaseModel):
    """浏览器在活动 Run 中追加的用户输入。"""

    content: str = Field(min_length=1, max_length=100_000)


class ResolveApprovalBody(BaseModel):
    """浏览器提交审批决定的 HTTP 请求体。"""

    approved: bool
