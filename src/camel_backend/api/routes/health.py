from fastapi import APIRouter


def create_health_router() -> APIRouter:
    """创建无需运行时依赖的健康检查路由。"""

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return router
