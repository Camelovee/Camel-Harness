from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest


async def _start_backend(
    tmp_path: Path, *, frontend_exists: bool = True
) -> asyncio.subprocess.Process:
    """用临时用户目录启动真实后端，不接触开发者的配置和会话。"""

    frontend_dist = tmp_path / "中文 界面"
    frontend_dist.mkdir()
    if frontend_exists:
        (frontend_dist / "index.html").write_text("<h1>Camel</h1>", encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "camel_backend.desktop",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )
    assert process.stdin is not None
    process.stdin.write(
        (
            json.dumps(
                {"token": "test-token-" * 5, "frontend_dist": str(frontend_dist)},
                ensure_ascii=False,
            )
            + "\n"
        ).encode()
    )
    await process.stdin.drain()
    return process


@pytest.mark.parametrize("close_pipe", [False, True])
async def test_desktop_real_process_authentication_and_shutdown(
    tmp_path: Path, close_pipe: bool
) -> None:
    """真实端口上的鉴权、静态资源和管道关闭必须形成完整生命周期。"""

    process = await _start_backend(tmp_path)
    try:
        assert process.stdout is not None
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        assert ready["type"] == "ready"
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False
        ) as client:
            assert (await client.get("/api/health")).status_code == 401
            denied = await client.get("/api/settings", headers={"X-Camel-Desktop-Token": "wrong"})
            assert denied.status_code == 401
            client.headers["X-Camel-Desktop-Token"] = "test-token-" * 5
            assert (await client.get("/api/health")).json() == {"status": "ok"}
            assert (await client.get("/api/desktop/status")).json() == {"active_runs": 0}
            assert (await client.get("/api/settings")).status_code == 200
            themed = await client.put("/api/settings/preferences", json={"theme": "dark"})
            assert themed.status_code == 200
            assert themed.json()["theme"] == "dark"
            assert json.loads((tmp_path / ".camel" / "config.json").read_text())["theme"] == "dark"
            assert (
                await client.put("/api/settings/preferences", json={"theme": "invalid"})
            ).status_code == 422
            assert (
                await client.get("/api/health", headers={"Origin": "https://untrusted.example"})
            ).status_code == 401
            assert (
                await client.get("/api/health", headers={"Host": "untrusted.example"})
            ).status_code == 401
            assert (await client.post("/api/desktop/exit")).json() == {"active_runs": 0}
            denied_run = await client.post("/api/sessions/missing/runs", json={"prompt": "测试"})
            assert denied_run.status_code == 409
            assert (await client.delete("/api/desktop/exit")).status_code == 204
            resumed_run = await client.post("/api/sessions/missing/runs", json={"prompt": "测试"})
            assert resumed_run.status_code == 404
            page = await client.get("/")
            assert page.text == "<h1>Camel</h1>"
            assert "script-src 'self'" in page.headers["content-security-policy"]
            assert (await client.get("/docs")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404
            assert (await client.get("/api/workspaces/select")).status_code == 404
            assert (await client.post("/api/workspaces/select")).status_code == 405
        assert process.stdin is not None
        if not close_pipe:
            process.stdin.write(b'{"type":"shutdown"}\n')
            await process.stdin.drain()
        else:
            process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 10) == 0
        assert b"test-token" not in (await process.communicate())[0]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_desktop_missing_frontend_fails_without_exposing_startup_input(
    tmp_path: Path,
) -> None:
    """损坏的安装包必须退出，协议错误不得回显临时凭据。"""

    process = await _start_backend(tmp_path, frontend_exists=False)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 1
    assert json.loads(stdout)["type"] == "error"
    assert b"test-token" not in stdout + stderr
