from fastapi import APIRouter, HTTPException

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
        try:
            await dependencies.require_settings().update_preferences(
                body.default_model_id, body.thinking
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await dependencies.require_settings().snapshot()

    @router.post("/settings/models", status_code=201)
    async def add_model(body: ModelInput) -> dict[str, object]:
        await dependencies.require_settings().add_model(body)
        return await dependencies.require_settings().snapshot()

    @router.put("/settings/models/{model_id}")
    async def update_model(model_id: str, body: ModelInput) -> dict[str, object]:
        try:
            await dependencies.require_settings().update_model(model_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        return await dependencies.require_settings().snapshot()

    @router.delete("/settings/models/{model_id}", status_code=204)
    async def delete_model(model_id: str) -> None:
        sessions = dependencies.require_sessions()
        related = [session for session in await sessions.list() if session.model_id == model_id]
        if any(
            (agent := dependencies.find_agent(session.session_id)) is not None and agent.is_running
            for session in related
        ):
            raise HTTPException(status_code=409, detail="使用此模型的会话运行结束后才能删除")
        try:
            await dependencies.require_settings().delete_model(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        replacement = (await dependencies.require_settings().snapshot())["default_model_id"]
        for session in related:
            await sessions.update(session.session_id, model_id=str(replacement))

    return router
