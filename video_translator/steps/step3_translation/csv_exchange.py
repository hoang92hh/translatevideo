"""Exchange Step 3 segments with a spreadsheet without exposing manifest metadata."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


CSV_COLUMNS = ("STT", "ID", "Start", "End", "Speaker", "Translation", "Source")
REQUIRED_COLUMNS = set(CSV_COLUMNS) - {"STT"}


def export_segments(rows: list[dict[str, Any]], destination: Path) -> None:
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow({
                "STT": index,
                "ID": row["id"],
                "Start": row["start"],
                "End": row["end"],
                "Speaker": row.get("speaker_id", ""),
                "Translation": row.get("translated_text", ""),
                "Source": row.get("source_text", row.get("text", "")),
            })


def import_segments(source: Path, original_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    original_by_id = {int(row["id"]): row for row in original_rows}
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            missing = ", ".join(sorted(REQUIRED_COLUMNS - set(reader.fieldnames or ())))
            raise ValueError(f"CSV thiếu cột: {missing}.")
        raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("CSV không có segment.")

    reserved_ids = set(original_by_id)
    for line, row in enumerate(raw_rows, start=2):
        value = str(row.get("ID") or "").strip()
        if value:
            try:
                segment_id = int(value)
            except ValueError as exc:
                raise ValueError(f"Dòng {line}: ID phải là số nguyên hoặc để trống.") from exc
            if segment_id <= 0:
                raise ValueError(f"Dòng {line}: ID phải lớn hơn 0.")
            reserved_ids.add(segment_id)

    next_id = max(reserved_ids, default=0) + 1
    seen_ids: set[int] = set()
    imported: list[dict[str, Any]] = []
    for line, row in enumerate(raw_rows, start=2):
        if None in row:
            raise ValueError(f"Dòng {line}: số cột vượt quá header CSV.")
        value = str(row.get("ID") or "").strip()
        if value:
            segment_id = int(value)
        else:
            segment_id = next_id
            next_id += 1
        if segment_id in seen_ids:
            raise ValueError(f"Dòng {line}: ID {segment_id} bị trùng.")
        seen_ids.add(segment_id)
        try:
            start = float(str(row.get("Start") or "").strip())
            end = float(str(row.get("End") or "").strip())
        except ValueError as exc:
            raise ValueError(f"Dòng {line}: Start/End phải là số giây.") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError(f"Dòng {line}: cần 0 ≤ Start ≤ End.")
        translation = str(row.get("Translation") or "").strip()
        if end > start and not translation:
            raise ValueError(f"Dòng {line}: Translation không được trống khi segment có thời lượng.")
        speaker = str(row.get("Speaker") or "").strip()
        if segment_id not in original_by_id and end > start and not speaker:
            raise ValueError(f"Dòng {line}: segment mới cần Speaker để tạo voice.")
        source_text = str(row.get("Source") or "")
        original = original_by_id.get(segment_id)
        imported.append({
            **(original or {}),
            "id": segment_id,
            "start": start,
            "end": end,
            "speaker_id": speaker,
            "source_text": source_text,
            "translated_text": translation,
            "merge_parts": list(original.get("merge_parts", [])) if original else [],
            **({"origin": "step3_csv_import"} if original is None else {}),
        })

    imported.sort(key=lambda item: (item["start"], item["end"], item["id"]))
    active = [item for item in imported if item["end"] > item["start"] and item["translated_text"]]
    for previous, following in zip(active, active[1:]):
        if previous["end"] > following["start"] + 0.000001:
            raise ValueError(f"ID {previous['id']} và {following['id']} chồng thời gian.")
    return imported
