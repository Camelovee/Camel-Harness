from pathlib import Path
from typing import cast

import pytest

from camel_harness.backend.api import HttpDependencies, create_router
from camel_harness.harness import Harness


def test_http_router_keeps_public_endpoints_after_module_split() -> None:
    """HTTP 内部拆分不得改变浏览器依赖的公开路径。"""

    router = create_router(HttpDependencies(Path.cwd()))

    assert str(router.url_path_for("health")) == "/api/health"
    assert str(router.url_path_for("sessions")) == "/api/sessions"
    assert str(router.url_path_for("get_session", session_id="s")) == "/api/sessions/s"
    assert (
        str(router.url_path_for("session_messages", session_id="s")) == "/api/sessions/s/messages"
    )
    assert str(router.url_path_for("run_turn", session_id="s")) == "/api/sessions/s/turns"
    assert str(router.url_path_for("workspaces")) == "/api/workspaces"
    assert str(router.url_path_for("select_workspace")) == "/api/workspaces/select"
    assert str(router.url_path_for("approvals")) == "/api/approvals"
    assert str(router.url_path_for("resolve_approval", request_id="r")) == "/api/approvals/r"


def test_http_dependencies_are_published_as_one_complete_set() -> None:
    """启动期依赖必须整体发布和清除，不允许路由观察到半初始化状态。"""

    dependencies = HttpDependencies(Path.cwd())
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        dependencies.require()

    harness = cast(Harness, object())
    dependencies.bind(harness)
    assert dependencies.require() is harness

    dependencies.clear()
    with pytest.raises(RuntimeError, match="尚未完成初始化"):
        dependencies.require()
