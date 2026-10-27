"""为公开安装包生成可跨平台核验的 SHA-256 清单。"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> None:
    """只对安装包和其更新块映射计算摘要，不纳入临时构建文件。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    directory: Path = arguments.directory
    artifacts = sorted(
        artifact
        for artifact in directory.iterdir()
        if artifact.is_file() and artifact.suffix in {".dmg", ".exe", ".blockmap"}
    )
    if not artifacts:
        parser.error("目录中没有桌面安装包")
    lines: list[str] = []
    for artifact in artifacts:
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {artifact.name}\n")
    (directory / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
