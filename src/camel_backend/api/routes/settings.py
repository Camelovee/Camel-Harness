from fastapi import APIRouter

from camel_backend.web_settings import ModelInput

from ..contracts import PreferencesBody
from ..dependencies import HttpDependencies


def create_settings_router(dependencies: HttpDependencies) -> APIRouter:
    """提供本机模型配置与 Web 默认偏好接口。"""

    router = APIRouter()

    @router.get("/settings")
    async def settings() -> dict[str, object]:
        return await dependencies.require_settings().snapshot()

    @router.put("/settings/preferences")
    async def update_preferences(body: PreferencesBody) -> dict[str, object]:
        await dependencies.require_settings().update_preferences(
            body.default_model_id, body.thinking
        )
        return await dependencies.require_settings().snapshot()

    @router.post("/settings/models", status_code=201)
    async def add_model(body: ModelInput) -> dict[str, object]:
        await dependencies.require_settings().add_model(body)
        return await dependencies.require_settings().snapshot()

    @router.put("/settings/models/{model_id}")
    async def update_model(model_id: str, body: ModelInput) -> dict[str, object]:
        await dependencies.require_settings().update_model(model_id, body)
        return await dependencies.require_settings().snapshot()

    @router.delete("/settings/models/{model_id}", status_code=204)
    async def delete_model(model_id: str) -> None:
        await dependencies.require_settings_service().delete_model(model_id)

    return router
