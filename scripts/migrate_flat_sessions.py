"""一次性复制旧平铺 JSONL 到会话目录；默认只预览，不删除源文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def plan_migration(root: Path) -> list[tuple[Path, Path]]:
    """根据首行父会话关系计算路径，拒绝缺失、循环或不安全 ID。"""

    root = root.expanduser().resolve()
    sources_by_session_id: dict[str, tuple[Path, dict[str, Any]]] = {}
    for source_path in sorted(root.glob("*.jsonl")):
        if source_path.is_symlink():
            raise ValueError(f"不迁移符号链接：{source_path}")
        with source_path.open(encoding="utf-8") as stream:
            header = json.loads(stream.readline())
        session_id = header.get("id")
        if (
            header.get("type") != "session"
            or not isinstance(session_id, str)
            or not SAFE_ID.fullmatch(session_id)
            or session_id != source_path.stem
        ):
            raise ValueError(f"会话头与文件名不匹配：{source_path}")
        sources_by_session_id[session_id] = (source_path, header)

    destinations_by_session_id: dict[str, Path] = {}

    def destination(session_id: str, visiting: set[str]) -> Path:
        if session_id in destinations_by_session_id:
            return destinations_by_session_id[session_id]
        if session_id in visiting:
            raise ValueError(f"父会话关系成环：{session_id}")
        source_header = sources_by_session_id.get(session_id)
        if source_header is None:
            raise ValueError(f"缺少父会话：{session_id}")
        parent_session_id = source_header[1].get("parentSessionId")
        if parent_session_id is None:
            directory = root / session_id
        elif isinstance(parent_session_id, str) and SAFE_ID.fullmatch(parent_session_id):
            directory = (
                destination(parent_session_id, visiting | {session_id}).parent
                / "subagents"
                / session_id
            )
        else:
            raise ValueError(f"父会话 ID 非法：{session_id}")
        session_path = directory / "session.jsonl"
        destinations_by_session_id[session_id] = session_path
        return session_path

    return [
        (source_path, destination(session_id, set()))
        for session_id, (source_path, _) in sources_by_session_id.items()
    ]


def _digest(session_path: Path) -> str:
    digest = hashlib.sha256()
    with session_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate(root: Path, apply: bool = False) -> list[str]:
    """已存在同内容目标时跳过，冲突时中止，绝不覆盖或删除旧日志。"""

    actions = []
    for source_path, target_path in plan_migration(root):
        if target_path.is_symlink():
            raise ValueError(f"目标不能是符号链接：{target_path}")
        if target_path.exists():
            if _digest(source_path) != _digest(target_path):
                raise ValueError(f"目标内容冲突，未覆盖：{target_path}")
            actions.append(f"已存在相同内容：{target_path}")
            continue
        actions.append(f"复制：{source_path} -> {target_path}")
        if apply:
            root_path = root.expanduser().resolve()
            current = target_path.parent
            while current != root_path:
                if current.is_symlink():
                    raise ValueError(f"目标目录不能是符号链接：{current}")
                current = current.parent
            target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source_path.open("rb") as reader, target_path.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            target_path.chmod(0o600)
            if _digest(source_path) != _digest(target_path):
                raise OSError(f"迁移校验失败：{target_path}")
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移旧平铺会话日志，默认只显示计划")
    parser.add_argument("--root", type=Path, default=Path.home() / ".camel" / "sessions")
    parser.add_argument("--apply", action="store_true", help="复制并校验；旧文件仍保留")
    arguments = parser.parse_args()
    for action in migrate(arguments.root, arguments.apply):
        print(action)


if __name__ == "__main__":
    main()
