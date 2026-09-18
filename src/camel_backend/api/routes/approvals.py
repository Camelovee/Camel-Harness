from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from ..contracts import ResolveApprovalBody
from ..dependencies import HttpDependencies


def create_approvals_router(dependencies: HttpDependencies) -> APIRouter:
    """创建待审批查询与处理路由。"""

    router = APIRouter()

    @router.get("/approvals")
    async def approvals() -> dict[str, Any]:
        approvals = dependencies.approvals()
        if approvals is None:
            return {"approvals": []}
        result = await approvals.list_pending()
        return {"approvals": [asdict(item) for item in result]}

    @router.post("/approvals/{request_id}")
    async def resolve_approval(request_id: str, body: ResolveApprovalBody) -> dict[str, bool]:
        approvals = dependencies.approvals()
        if approvals is None:
            raise HTTPException(status_code=404, detail="当前入口不支持浏览器审批")
        resolved = await approvals.resolve(request_id, body.approved)
        if not resolved:
            raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
        return {"resolved": True}

    return router
