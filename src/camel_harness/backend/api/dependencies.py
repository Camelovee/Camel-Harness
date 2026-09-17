from dataclasses import dataclass, field
from pathlib import Path

from camel_harness.harness import Harness


@dataclass(slots=True)
class HttpDependencies:
    """保存入口默认工作区，并原子发布启动期创建的 Harness。"""

    workspace: Path
    _harness: Harness | None = field(default=None, init=False, repr=False)

    def bind(self, harness: Harness) -> None:
        """一次发布完整 Harness，避免请求观察到部分初始化状态。"""

        self._harness = harness

    def clear(self) -> None:
        """在应用停止后清除已关闭的运行时依赖。"""

        self._harness = None

    def require(self) -> Harness:
        """返回已装配 Harness；应用尚未就绪时快速失败。"""

        if self._harness is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._harness
