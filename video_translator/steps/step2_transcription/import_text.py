"""Read plain text and SRT as source speech for Step 2 transcripts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_TIMESTAMP = r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{1,3})"
_CUE_TIME = re.compile(rf"^\s*{_TIMESTAMP}\s*-->\s*{_TIMESTAMP}(?:\s+.*)?$")


def _read_text(path: Path) -> str:
    content = path.read_bytes()
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError(f"Không đọc được {path.name}: hãy lưu file bằng UTF-8 hoặc UTF-16.") from exc


def read_txt(path: Path, original_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines = [line.strip() for line in _read_text(path).splitlines() if line.strip()]
    if len(lines) != len(original_rows):
        raise ValueError(
            f"TXT có {len(lines)} dòng không trắng, transcript đang chọn có "
            f"{len(original_rows)} segment. Cần đúng một dòng cho mỗi segment."
        )
    return [
        {**row, "text": line, **({"source_text": line} if "source_text" in row else {})}
        for row, line in zip(original_rows, lines, strict=True)
    ]


def _seconds(parts: tuple[str, ...]) -> float:
    hours, minutes, seconds, milliseconds = parts
    return round(
        int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        + int(milliseconds.ljust(3, "0")) / 1000,
        3,
    )


def read_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", _read_text(path).replace("\r\n", "\n").strip())
    if not blocks or not blocks[0].strip():
        raise ValueError("SRT không có mục phụ đề nào.")
    segments: list[dict[str, Any]] = []
    for cue_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines()]
        if lines and lines[0].isdigit():
            lines = lines[1:]
        match = _CUE_TIME.fullmatch(lines[0]) if lines else None
        if match is None:
            raise ValueError(f"Mục SRT {cue_number}: thiếu mốc thời gian 'HH:MM:SS,mmm --> HH:MM:SS,mmm'.")
        start = _seconds(match.groups()[:4])
        end = _seconds(match.groups()[4:])
        if end <= start:
            raise ValueError(f"Mục SRT {cue_number}: End phải lớn hơn Start.")
        if segments and start < segments[-1]["end"]:
            raise ValueError(f"Mục SRT {cue_number}: thời gian chồng lên mục trước hoặc sai thứ tự.")
        text = " ".join(line for line in lines[1:] if line)
        if not text:
            raise ValueError(f"Mục SRT {cue_number}: nội dung text đang trống.")
        segments.append({
            "id": cue_number,
            "start": start,
            "end": end,
            "speaker_id": "SPEAKER_UNKNOWN",
            "merge_parts": [],
            "text": text,
        })
    return segments
