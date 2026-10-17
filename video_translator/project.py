from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_FILE = "project.json"
VIDEO_PROJECT_FILE = "video.json"
PROJECT_FOLDERS = (
    "input",
    "extracted",
    "audio_separation",
    "transcripts",
    "translations",
    "generated_audio",
    "synchronized_audio",
    "built_audio",
    "subtitles",
    "temp",
    "output",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class VideoProject:
    id: str
    name: str
    root: str
    input_video: str
    original_video: str
    source_language: str
    target_language: str
    created_at: str
    updated_at: str
    parent_project_id: str = ""
    video_id: str = ""
    output_root: str = ""
    pipeline: dict[str, Any] = field(default_factory=dict)
    translation_project_urls: dict[str, str] = field(default_factory=dict)
    audio_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_audio_candidate_id: str = ""
    selected_audio_stem: str = ""
    default_audio_candidate_id: str = ""
    default_audio_stem: str = ""
    transcript_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_transcript_candidate_id: str = ""
    default_transcript_candidate_id: str = ""
    translation_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_translation_candidate_id: str = ""
    default_translation_candidate_id: str = ""
    tts_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_tts_candidate_id: str = ""
    default_tts_candidate_id: str = ""
    sync_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_sync_candidate_id: str = ""
    default_sync_candidate_id: str = ""
    build_audio_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_build_audio_candidate_id: str = ""
    default_build_audio_candidate_id: str = ""
    render_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_render_candidate_id: str = ""

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    @property
    def manifest_path(self) -> Path:
        return self.root_path / (VIDEO_PROJECT_FILE if self.parent_project_id else PROJECT_FILE)

    def path(self, *parts: str) -> Path:
        if parts and parts[0] == "output" and self.output_root:
            return Path(self.output_root).joinpath(*parts[1:])
        return self.root_path.joinpath(*parts)

    def save(self) -> None:
        self.updated_at = _utc_now()
        self.manifest_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
