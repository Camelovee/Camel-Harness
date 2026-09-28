"""把领域异常统一映射为 HTTP 错误响应，路由内不再逐个转换。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from camel_harness.agent import AgentBusyError, AgentNotRunningError, AgentResumeError


def install_exception_handlers(app: FastAPI) -> None:
    """注册全部领域异常到状态码的映射，一次性替代路由内的 try/except。"""

    app.add_exception_handler(KeyError, _not_found)
    app.add_exception_handler(ValueError, _unprocessable)
    app.add_exception_handler(AgentBusyError, _conflict)
    app.add_exception_handler(AgentNotRunningError, _conflict)
    app.add_exception_handler(AgentResumeError, _conflict)
    app.add_exception_handler(OverflowError, _too_many_requests)
    app.add_exception_handler(RuntimeError, _unavailable)


async def _not_found(_: Request, exc: Exception) -> JSONResponse:
    detail = str(exc.args[0]) if exc.args else "资源不存在"
    return JSONResponse(status_code=404, content={"detail": detail})


async def _unprocessable(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def _conflict(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _too_many_requests(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(exc)})


async def _unavailable(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})
