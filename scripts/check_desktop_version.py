"""保证 Python、桌面工程和发布标签使用同一版本号。"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def main() -> None:
    """普通 CI 检查工程版本，版本标签构建额外校验标签。"""

    repository_root = Path(__file__).resolve().parents[1]
    with (repository_root / "pyproject.toml").open("rb") as stream:
        version: str = tomllib.load(stream)["project"]["version"]
    desktop_version = json.loads(
        (repository_root / "desktop/package.json").read_text(encoding="utf-8")
    )["version"]
    if desktop_version != version:
        raise ValueError("desktop/package.json 的版本号与 Python 项目不一致")
    if len(sys.argv) > 1 and sys.argv[1].startswith("v") and sys.argv[1] != f"v{version}":
        raise ValueError("发布标签与项目版本号不一致")
    print(f"桌面版本校验通过：{version}")


if __name__ == "__main__":
    main()
