"""Export and reimport an existing Step 2 transcript as a five-column CSV."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


CSV_COLUMNS = ("ID", "Speaker", "Start", "End", "Source")


def export_segments(rows: list[dict[str, Any]], destination: Path) -> None:
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "ID": row["id"],
                "Speaker": row.get("speaker_id", ""),
                "Start": row["start"],
                "End": row["end"],
                "Source": row.get("text", row.get("source_text", "")),
            })


def import_segments(source: Path, original_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    original_by_id = {int(row["id"]): row for row in original_rows}
    if not original_rows:
        raise ValueError("Output Step 2 nguồn không có segment để nhập CSV.")
    if len(original_by_id) != len(original_rows):
        raise ValueError("Output Step 2 nguồn có ID segment trùng nhau.")
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if (not reader.fieldnames or len(reader.fieldnames) != len(CSV_COLUMNS)
                or set(reader.fieldnames) != set(CSV_COLUMNS)):
            raise ValueError("CSV Step 2 cần đúng 5 cột: ID, Speaker, Start, End, Source.")
        raw_rows = list(reader)
    if len(raw_rows) != len(original_rows):
        raise ValueError("CSV phải giữ nguyên số segment của output Step 2 nguồn.")

    imported_by_id: dict[int, dict[str, Any]] = {}
    for line_number, row in enumerate(raw_rows, start=2):
        if None in row:
            raise ValueError(f"Dòng {line_number}: số cột vượt quá header CSV.")
        try:
            segment_id = int(str(row.get("ID") or "").strip())
        except ValueError as exc:
            raise ValueError(f"Dòng {line_number}: ID phải là ID segment hiện có.") from exc
        if segment_id not in original_by_id or segment_id in imported_by_id:
            raise ValueError(f"Dòng {line_number}: ID {segment_id} không có trong output nguồn hoặc bị trùng.")
        try:
            start = float(str(row.get("Start") or "").strip())
            end = float(str(row.get("End") or "").strip())
        except ValueError as exc:
            raise ValueError(f"Dòng {line_number}: Start/End phải là số giây.") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError(f"Dòng {line_number}: cần 0 ≤ Start ≤ End.")
        text = str(row.get("Source") or "")
        if not text.strip():
            raise ValueError(f"Dòng {line_number}: Source không được trống.")
        original = original_by_id[segment_id]
        imported_by_id[segment_id] = {
            **original,
            "id": segment_id,
            "speaker_id": str(row.get("Speaker") or ""),
            "start": start,
            "end": end,
            "text": text,
        }

    missing = set(original_by_id) - set(imported_by_id)
    if missing:
        raise ValueError(f"CSV thiếu ID segment: {', '.join(map(str, sorted(missing)))}.")
    return [imported_by_id[int(row["id"])] for row in original_rows]
