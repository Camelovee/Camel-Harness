from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from io import TextIOWrapper
from pathlib import Path

import uvicorn
from pydantic import BaseModel, Field

from camel_backend.main import create_app


class DesktopStartup(BaseModel):
    """只通过父进程管道接收临时凭据，避免进入命令行和日志。"""

    token: str = Field(min_length=32, max_length=128, repr=False)
    frontend_dist: Path


def _notify(payload: dict[str, object]) -> None:
    """标准输出只交付协议帧，诊断日志使用标准错误。"""

    print(json.dumps(payload, ensure_ascii=False), flush=True)


async def run_backend(startup: DesktopStartup) -> None:
    """启动私有本机服务，并在父进程退出或请求关闭时清理运行资源。"""

    frontend_dist = startup.frontend_dist.resolve(strict=True)
    if not (frontend_dist / "index.html").is_file():
        raise ValueError("桌面界面资源缺失，请重新安装应用")
    application = create_app(frontend_dist=frontend_dist, desktop_token=startup.token)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()

    def read_control() -> None:
        # 使用守护线程，启动失败时不会因等待 stdin 而阻止 Python 退出。
        for line in sys.stdin:
            try:
                if json.loads(line) != {"type": "shutdown"}:
                    continue
            except ValueError:
                continue
            break
        try:
            loop.call_soon_threadsafe(stopped.set)
        except RuntimeError:
            pass

    threading.Thread(target=read_control, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                log_level="warning",
                access_log=False,
                loop="asyncio",
                http="h11",
                ws="none",
                lifespan="on",
            )
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started and not server_task.done() and not stopped.is_set():
                await asyncio.sleep(0.02)
            if server.started and not stopped.is_set():
                _notify({"type": "ready", "port": listener.getsockname()[1]})
            stop_task = asyncio.create_task(stopped.wait())
            try:
                await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
        finally:
            # 先取消 Agent，再停止 HTTP，避免 Uvicorn 一直等待仍打开的 SSE。
            try:
                await application.state.dependencies.close()
            finally:
                server.should_exit = True
                await server_task


def main() -> None:
    """桌面专用入口，不读取环境变量或持久化临时凭据。"""

    # 管道协议固定 UTF-8，不依赖 Windows 控制台代码页或启动器环境。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        startup = DesktopStartup.model_validate_json(sys.stdin.readline())
        asyncio.run(run_backend(startup))
    except Exception:
        # 校验异常可能包含原始输入，不能直接记录异常或 traceback。
        _notify({"type": "error", "message": "无法启动本地后端，请检查配置或重新安装应用"})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
