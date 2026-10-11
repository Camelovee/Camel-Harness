import shlex
import subprocess
import sys

import pytest


@pytest.fixture
def python_command():
    """测试命令按实际 Shell 引用参数，不依赖 PATH 或 Unix 命令。"""

    def build(*arguments):
        argv = [sys.executable, *map(str, arguments)]
        return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)

    return build
