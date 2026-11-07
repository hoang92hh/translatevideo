from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4


_GROUP_HEADER = re.compile(r"^> Group: \d+\. Kích thước và thành viên Group đi theo STT đã lưu của project\.$")
_SCOPE_HEADER = re.compile(r"^> Phạm vi: các video có STT .+ tại lúc bắt đầu batch\.$")
_COVERAGE_LINE = re.compile(
    r"^> (?:Video \d+: |Chưa có lời thoại của video STT |Đã dùng lời thoại của video STT )"
)


def without_legacy_story_coverage(content: str) -> str:
    """Remove only the application-generated header used by older story files."""
    lines = content.splitlines(keepends=True)
    if len(lines) < 2 or not _GROUP_HEADER.fullmatch(lines[0].rstrip("\r\n")):
        return content
    if not _SCOPE_HEADER.fullmatch(lines[1].rstrip("\r\n")):
        return content
    index = 2
    while index < len(lines) and _COVERAGE_LINE.match(lines[index].rstrip("\r\n")):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "".join(lines[index:])


def load_story_content(path: Path) -> str:
    """Read a story and persist removal of its legacy coverage header."""
    content = path.read_text(encoding="utf-8-sig")
    cleaned = without_legacy_story_coverage(content)
    if cleaned == content:
        return content
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.part")
    try:
        temporary.write_text(cleaned, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return cleaned
