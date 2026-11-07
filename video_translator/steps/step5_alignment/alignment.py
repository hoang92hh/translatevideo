from __future__ import annotations

from typing import Any


VIDEO_EXTENSION_MODES = {"Freeze Frame", "Slow Motion", "Hybrid"}


def build_video_timing_plan(
    segments: list[dict[str, Any]],
    source_duration: float,
    *,
    enabled: bool,
    mode: str,
    min_video_speed: float,
    max_freeze_duration: float,
) -> dict[str, Any]:
    """Apply cumulative video offsets to the Step 5 audio schedule.

    The source timestamps remain untouched.  Only segments whose prepared voice
    exceeds their original slot (including an allowed following gap) create a
    local video edit; later segments inherit the accumulated offset.
    """
    selected_mode = mode if mode in VIDEO_EXTENSION_MODES else "Hybrid"
    minimum_speed = max(0.25, min(1.0, float(min_video_speed)))
    freeze_limit = max(0.0, float(max_freeze_duration))
    cumulative_extension = 0.0
    edits: list[dict[str, Any]] = []

    for item in segments:
        source_start = max(0.0, float(item.get("start", 0.0)))
        source_end = max(source_start, float(item.get("end", source_start)))
        if "audio_adjusted_start" not in item:
            item["audio_adjusted_start"] = float(item.get("adjusted_start", source_start))
        audio_start = float(item.get("audio_adjusted_start", source_start))
        play_duration = max(0.0, float(item.get("play_duration", 0.0)))
        item["video_extension_duration"] = 0.0
        item["video_slow_duration"] = 0.0
        item["video_freeze_duration"] = 0.0
        item["video_speed"] = 1.0

        previous_video_limit = item.get("sync_error_type") == "video_extension_limit"
        if previous_video_limit:
            item["status"] = "ready"
            item["repair_status"] = "resolved"
            item["error"] = ""
            item["sync_error_type"] = ""

        allowed_duration = max(0.0, float(item.get("allowed_duration", source_end - source_start)))
        following_gap = max(0.0, allowed_duration - (source_end - source_start))
        audio_strategy = str(item.get("sync_strategy", ""))
        if audio_strategy.startswith("video_"):
            audio_strategy = "local"
            item["sync_strategy"] = "local"
        overflow = (
            0.0
            if audio_strategy.startswith("neighbor_")
            else max(0.0, play_duration - allowed_duration)
        )
        extension = 0.0
        slow_extension = 0.0
        freeze_extension = 0.0

        if enabled and item.get("status") == "ready" and overflow > 0.002:
            source_segment_duration = max(0.01, source_end - source_start)
            if selected_mode in {"Slow Motion", "Hybrid"}:
                slow_capacity = source_segment_duration / minimum_speed - source_segment_duration
                slow_extension = min(overflow, max(0.0, slow_capacity))
            remaining = max(0.0, overflow - slow_extension)
            if selected_mode in {"Freeze Frame", "Hybrid"}:
                freeze_extension = min(remaining, freeze_limit)
                remaining -= freeze_extension

            if remaining > 0.002:
                if not previous_video_limit:
                    item["seq"] = int(item.get("seq", 0)) + 1
                    item["initial_sync_error"] = True
                item["status"] = "needs_edit"
                item["repair_status"] = "too_long"
                item["sync_error_type"] = "video_extension_limit"
                item["error"] = (
                    f"Voice còn vượt {remaining:.2f}s sau giới hạn {selected_mode}; "
                    "cần rút gọn bản dịch hoặc tăng giới hạn kéo dài video."
                )
            else:
                extension = overflow
                video_speed = source_segment_duration / (source_segment_duration + slow_extension)
                strategy = (
                    "hybrid"
                    if slow_extension > 0.002 and freeze_extension > 0.002
                    else "slow_motion"
                    if slow_extension > 0.002
                    else "freeze_frame"
                )
                item["sync_strategy"] = f"video_{strategy}"
                item["video_extension_duration"] = round(extension, 6)
                item["video_slow_duration"] = round(slow_extension, 6)
                item["video_freeze_duration"] = round(freeze_extension, 6)
                item["video_speed"] = round(video_speed, 6)
                freeze_at_source = (
                    source_end + following_gap / 2.0
                    if freeze_extension > 0.002 and following_gap >= 0.12
                    else source_end
                )
                edits.append(
                    {
                        "segment_id": int(item.get("id", 0)),
                        "source_start": round(source_start, 6),
                        "source_end": round(source_end, 6),
                        "strategy": strategy,
                        "video_speed": round(video_speed, 6),
                        "slow_duration": round(slow_extension, 6),
                        "freeze_duration": round(freeze_extension, 6),
                        "freeze_at_source": round(freeze_at_source, 6),
                        "extension_duration": round(extension, 6),
                    }
                )

        item["timeline_offset"] = round(cumulative_extension, 6)
        item["adjusted_start"] = round(audio_start + cumulative_extension, 6)
        item["adjusted_end"] = round(audio_start + cumulative_extension + play_duration, 6)
        cumulative_extension += extension

    return {
        "enabled": bool(enabled),
        "mode": selected_mode,
        "min_video_speed": round(minimum_speed, 6),
        "max_freeze_duration": round(freeze_limit, 6),
        "source_duration": round(max(0.0, source_duration), 6),
        "output_duration": round(max(0.0, source_duration) + cumulative_extension, 6),
        "total_extension": round(cumulative_extension, 6),
        "edits": edits,
    }
