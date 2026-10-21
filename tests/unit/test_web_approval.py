import asyncio

from camel_backend.approval import WebApproval
from camel_harness.agent.tools import PermissionMode, PermissionRequest


async def test_web_approval_can_be_resolved_from_pending_queue() -> None:
    """浏览器审批应挂起工具调用，并在用户决定后恢复。"""

    web_approval = WebApproval(timeout_seconds=1)
    permission_request = PermissionRequest(
        "write_file", frozenset({"write"}), "write", {"path": "a"}, PermissionMode.DEFAULT
    )
    task = asyncio.create_task(web_approval.approve(permission_request, "需要修改工作区"))
    await asyncio.sleep(0)

    pending = await web_approval.list_pending()
    assert len(pending) == 1
    assert pending[0].tool_name == "write_file"
    assert await web_approval.resolve(pending[0].request_id, True)
    assert await task
    assert await web_approval.list_pending() == ()
