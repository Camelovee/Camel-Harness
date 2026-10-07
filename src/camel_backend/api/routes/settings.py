from fastapi import APIRouter
from pydantic import BaseModel

from camel_backend.web_settings import ModelInput, Provider

from ..contracts import PreferencesBody
from ..dependencies import HttpDependencies


class ConfigBody(BaseModel):
    """提交配置文件默认模型的表单值与读取版本。"""

    provider: Provider
    model: str
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    revision: str


def create_settings_router(dependencies: HttpDependencies) -> APIRouter:
    """提供本机模型配置与 Web 默认偏好接口。"""

    router = APIRouter()

    @router.get("/settings/config")
    async def config() -> dict[str, object]:
        return await dependencies.require_settings_service().get_config()

    @router.put("/settings/config")
    async def save_config(body: ConfigBody) -> dict[str, object]:
        return await dependencies.require_settings_service().save_config(
            revision=body.revision,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
        )

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
