"""在原始文本快照上验证批量替换，保留未修改区域的字节表示。"""

from __future__ import annotations

import difflib
import re

from .types import ToolNotExecutedError


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """将换行统一为 LF，并映射规范化字符边界到原文位置。"""

    characters: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(text):
        offsets.append(index)
        character = text[index]
        characters.append("\n" if character == "\r" else character)
        index += 2 if text.startswith("\r\n", index) else 1
    offsets.append(len(text))
    return "".join(characters), offsets


def apply_text_edits(content: str, edits: list[dict[str, str]], path: str) -> tuple[str, str]:
    """所有片段匹配原始快照，验证全部通过后才生成新内容及 diff。"""

    if not edits:
        raise ToolNotExecutedError("edits 至少包含一项替换，未执行修改")
    # 下一行：BOM 剥离后按原文回挂；行尾取文件中第一个换行，混合行尾会被统一成它。
    bom = "\ufeff" if content.startswith("\ufeff") else ""
    original = content[len(bom) :]
    normalized, offsets = _normalize_with_offsets(original)
    ending_match = re.search(r"\r\n|\r|\n", original)
    ending = ending_match.group() if ending_match else "\n"
    replacements: list[tuple[int, int, str]] = []
    for index, edit in enumerate(edits):
        # 模型给的 CRLF 也要归一，否则无法匹配已经规范化的原文。
        old_text = re.sub(r"\r\n?", "\n", edit["old_text"])
        new_text = re.sub(r"\r\n?", "\n", edit["new_text"])
        if not old_text:
            raise ToolNotExecutedError(f"edits[{index}].old_text 不能为空，未执行修改")
        start = normalized.find(old_text)
        if start < 0 or normalized.find(old_text, start + 1) >= 0:
            raise ToolNotExecutedError(
                f"edits[{index}].old_text 必须在原始文件中唯一匹配；请重新读取并调整，未执行修改"
            )
        # 新文本采用文件现有行尾，避免一次编辑把整文件改成 CRLF 或 LF。
        replacements.append((start, start + len(old_text), new_text.replace("\n", ending)))
    replacements.sort(key=lambda replacement: replacement[0])
    for previous, current in zip(replacements, replacements[1:], strict=False):
        if current[0] < previous[1]:
            raise ToolNotExecutedError("替换区域重叠或嵌套，请合并为一项；未执行修改")
    updated = original
    # 逆序替换保证前面的 offsets 仍然有效，不需要每改一次就重算映射。
    for start, end, new_text in reversed(replacements):
        updated = updated[: offsets[start]] + new_text + updated[offsets[end] :]
    updated = bom + updated
    if updated == content:
        raise ToolNotExecutedError("替换没有产生实际变化，未执行修改")
    # diff 使用规范化文本，避免整文件 CRLF/LF 差异淹没真正的修改。
    normalized_after = re.sub(r"\r\n?", "\n", updated[len(bom) :])
    return updated, _unified_diff(normalized, normalized_after, path)


def _unified_diff(before: str, after: str, path: str) -> str:
    """显式保留文件末尾缺少换行的信息，避免漏掉仅新增末尾换行的修改。"""

    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
    )
    output: list[str] = []
    for line in lines:
        output.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(output)
