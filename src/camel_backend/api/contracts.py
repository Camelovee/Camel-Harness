from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from camel_harness.agent.session import SessionMetadata


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
    permission_mode: Literal["default", "plan", "yolo"] = "default"
    max_turns: int = Field(default=20, ge=1, le=100)
    effort: Literal["low", "high", "max"] = "low"
    model_id: str | None = Field(default=None, min_length=1, max_length=64)


class ResumeBody(BaseModel):
    """浏览器恢复一个可继续 Session 的请求体。"""

    permission_mode: Literal["default", "plan", "yolo"] = "default"
    max_turns: int = Field(default=20, ge=1, le=100)
    effort: Literal["low", "high", "max"] = "low"
    model_id: str | None = Field(default=None, min_length=1, max_length=64)


class PreferencesBody(BaseModel):
    """用户的 Web 默认模型。"""

    default_model_id: str = Field(min_length=1, max_length=64)


class SteerBody(BaseModel):
    """浏览器在活动 Run 中追加的用户输入。"""

    content: str = Field(min_length=1, max_length=100_000)


class ResolveApprovalBody(BaseModel):
    """浏览器提交审批决定的 HTTP 请求体。"""

    approved: bool


class SessionResponse(BaseModel):
    """浏览器可见的会话投影；字段与会话元数据一一对应，不泄露存储细节之外的信息。"""

    session_id: str
    name: str
    workspace: str
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_type: str | None = None
    model_id: str | None = None
    archived: bool = False
    parent_session_id: str | None = None
    parent_tool_call_id: str | None = None
    schema_version: int = 1

    @classmethod
    def from_metadata(cls, metadata: SessionMetadata) -> SessionResponse:
        return cls(**asdict(metadata))


class SessionsResponse(BaseModel):
    """会话列表响应。"""

    sessions: list[SessionResponse]


class SessionMessagesResponse(BaseModel):
    """会话历史消息响应；消息体保持规范消息的 JSON 形状。"""

    messages: list[dict[str, Any]]
    session_id: str


class WorkspaceGroupResponse(BaseModel):
    """一个工作区及其所属会话的分组投影。"""

    workspace: str
    updated_at: str
    sessions: list[SessionResponse]


class WorkspacesResponse(BaseModel):
    """按工作区分组的会话列表响应。"""

    workspaces: list[WorkspaceGroupResponse]


class SteerQueuedResponse(BaseModel):
    """运行中输入已入队的确认响应。"""

    input_id: str
    run_id: str
    status: str
