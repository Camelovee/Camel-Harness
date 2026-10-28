import re
from pathlib import Path

RENDERER_ROOT = Path(__file__).parents[2] / "desktop" / "renderer" / "src"
IMPORT_PATTERN = re.compile(r"from\s+[\"'](?P<target>\.{1,2}/[^\"']+)[\"']")


def _relative_imports(path: Path) -> list[Path]:
    imports: list[Path] = []
    for match in IMPORT_PATTERN.finditer(path.read_text(encoding="utf-8")):
        imports.append((path.parent / match.group("target")).resolve())
    return imports


def test_desktop_renderer_dependencies_point_from_app_to_features_to_shared() -> None:
    """React 分层只能向内依赖，feature 之间由 app 组合而不直接耦合。"""

    violations: list[str] = []
    for path in sorted(RENDERER_ROOT.rglob("*.ts*")):
        relative = path.relative_to(RENDERER_ROOT)
        owner = relative.parts[0]
        feature = relative.parts[1] if owner == "features" else None
        for imported in _relative_imports(path):
            if not imported.is_relative_to(RENDERER_ROOT):
                continue
            target = imported.relative_to(RENDERER_ROOT).parts
            if owner == "shared" and target[0] in {"app", "features"}:
                violations.append(f"{relative} -> {'/'.join(target)}")
            if owner == "features" and target[0] == "app":
                violations.append(f"{relative} -> {'/'.join(target)}")
            if (
                owner == "features"
                and target[0] == "features"
                and len(target) > 1
                and target[1] != feature
            ):
                violations.append(f"{relative} -> {'/'.join(target)}")

    assert not violations
