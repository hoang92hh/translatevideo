"""Presentation settings and readable cue splitting for Step 7 subtitles."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


ALIGNMENTS = {
    "bottom_left": 1,
    "bottom_center": 2,
    "bottom_right": 3,
    "middle_center": 5,
    "top_center": 8,
}


def subtitle_dimensions(settings: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    scale = height / 1080
    font_size = round(52 * scale) if settings["subtitle_auto_size"] else int(settings["subtitle_font_size"])
    outline = round(int(settings["subtitle_outline_size"]) * scale)
    shadow = round(int(settings["subtitle_shadow_size"]) * scale)
    margin = round(int(settings["subtitle_margin"]) * scale)
    return max(12, font_size), max(0, outline), max(0, shadow), max(0, margin)


def _ass_color(value: str, alpha: str = "00") -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"Màu phụ đề không hợp lệ: {value}. Hãy nhập dạng #RRGGBB.")
    return f"&H{alpha}{value[5:7]}{value[3:5]}{value[1:3]}"


def subtitle_ass_values(settings: dict[str, Any], width: int, height: int) -> dict[str, str | int]:
    font = str(settings["subtitle_font"]).strip()
    if not re.fullmatch(r"[\w .-]{1,80}", font, flags=re.UNICODE):
        raise ValueError("Tên font phụ đề chỉ được gồm chữ, số, dấu cách, dấu chấm và gạch nối.")
    position = str(settings["subtitle_position"])
    if position not in ALIGNMENTS:
        raise ValueError("Vị trí phụ đề không hợp lệ.")
    size, outline, shadow, margin = subtitle_dimensions(settings, width, height)
    values = {
        "FontName": font,
        "FontSize": size,
        "PrimaryColour": _ass_color(str(settings["subtitle_text_color"])),
        "OutlineColour": _ass_color(str(settings["subtitle_outline_color"])),
        "BackColour": _ass_color(str(settings["subtitle_shadow_color"]), "80"),
        "BorderStyle": 1,
        "Outline": outline,
        "Shadow": shadow,
        "Alignment": ALIGNMENTS[position],
        "MarginV": margin,
        "MarginL": margin,
        "MarginR": margin,
        "WrapStyle": 2,
    }
    return values


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{fraction:02d}"


def write_styled_ass(
    path, cues: list[tuple[float, float, str]], settings: dict[str, Any],
    width: int, height: int,
) -> None:
    values = subtitle_ass_values(settings, width, height)
    style_fields = (
        "Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,"
        "Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding"
    )
    style = ",".join(str(value) for value in (
        "Default", values["FontName"], values["FontSize"],
        values["PrimaryColour"], values["PrimaryColour"],
        values["OutlineColour"], values["BackColour"],
        0, 0, 0, 0, 100, 100, 0, 0, values["BorderStyle"],
        values["Outline"], values["Shadow"], values["Alignment"],
        values["MarginL"], values["MarginR"], values["MarginV"], 1,
    ))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}",
        f"PlayResY: {height}", "ScaledBorderAndShadow: yes", "WrapStyle: 2", "",
        "[V4+ Styles]", f"Format: {style_fields}", f"Style: {style}", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for start, end, text in cues:
        escaped = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,{escaped}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _unit_width(text: str) -> float:
    return sum(2.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 1.0 for char in text)


def _wrap_lines(text: str, max_units: int) -> list[str]:
    words = text.replace("\r", " ").replace("\n", " ").split()
    lines: list[str] = []
    line = ""
    for word in words:
        while _unit_width(word) > max_units:
            if line:
                lines.append(line)
                line = ""
            cut = 0
            used = 0.0
            for char in word:
                next_width = 2.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 1.0
                if cut and used + next_width > max_units:
                    break
                used += next_width
                cut += 1
            lines.append(word[:cut])
            word = word[cut:]
        candidate = f"{line} {word}" if line else word
        if line and _unit_width(candidate) > max_units:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def split_readable_cue(
    start: float, end: float, text: str, *, width: int, font_size: int, max_lines: int,
) -> list[tuple[float, float, str]]:
    max_units = max(8, int(width * 0.88 / max(1, font_size) / 0.56))
    lines = _wrap_lines(text, max_units)
    groups = [lines[index:index + max_lines] for index in range(0, len(lines), max_lines)]
    if not groups:
        return []
    weights = [sum(_unit_width(line) for line in group) for group in groups]
    total = sum(weights) or len(groups)
    cursor = start
    result: list[tuple[float, float, str]] = []
    for index, (group, weight) in enumerate(zip(groups, weights, strict=True)):
        finish = end if index == len(groups) - 1 else cursor + (end - start) * weight / total
        result.append((cursor, finish, "\n".join(group)))
        cursor = finish
        total -= weight
    return result
