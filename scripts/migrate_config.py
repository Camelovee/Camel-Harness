"""一次性转换旧用户配置；运行时不读取 YAML，不覆盖已有 JSON。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

from camel_harness.agent.config import AgentSettings


def migrate(source: Path, target: Path) -> None:
    """保留模型、凭据和存储设置，废弃旧压缩字段并使用新的独立默认预算。"""

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("旧配置顶层必须是对象")
    for field in (
        "max_context_chars",
        "compaction_min_messages",
        "max_compaction_tokens",
        "max_total_tokens",
    ):
        data.pop(field, None)
    settings = AgentSettings.model_validate(data)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=".config-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(settings.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    source = Path.home() / ".camel" / "config.yaml"
    target = Path.home() / ".camel" / "config.json"
    try:
        migrate(source, target)
    except FileExistsError:
        sys.exit("config.json 已存在，未覆盖；请手动核对配置")
    except Exception:
        # 解析器与校验器可能携带原文，CLI 不输出异常或密钥。
        sys.exit("配置转换失败；旧文件未修改，请检查格式、字段和目录权限")
    print("已创建 ~/.camel/config.json（0600）；旧 YAML 保留，运行时不再读取它")
