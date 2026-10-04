from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_FILE = "project.json"
PROJECT_FOLDERS = (
    "input",
    "extracted",
    "audio_separation",
    "transcripts",
    "translations",
    "generated_audio",
    "synchronized_audio",
    "subtitles",
    "temp",
    "output",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _folder_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized).strip("-_").lower()
    return normalized or f"project-{uuid4().hex[:8]}"


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
    pipeline: dict[str, Any] = field(default_factory=dict)
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

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    @property
    def manifest_path(self) -> Path:
        return self.root_path / PROJECT_FILE

    def path(self, *parts: str) -> Path:
        return self.root_path.joinpath(*parts)

    def save(self) -> None:
        self.updated_at = _utc_now()
        self.manifest_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ProjectService:
    @staticmethod
    def create(
        name: str,
        source_video: str,
        parent_folder: str,
        source_language: str,
        target_language: str,
    ) -> VideoProject:
        video_path = Path(source_video).expanduser().resolve()
        if not video_path.is_file():
            raise ValueError("Video nguồn không tồn tại hoặc không phải là file.")
        parent = Path(parent_folder).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        root = parent / _folder_name(name)
        if root.exists():
            raise FileExistsError(f"Thư mục project đã tồn tại: {root}")

        root.mkdir()
        try:
            for folder in PROJECT_FOLDERS:
                (root / folder).mkdir()
            copied_video = root / "input" / video_path.name
            shutil.copy2(video_path, copied_video)
            now = _utc_now()
            project = VideoProject(
                id=uuid4().hex,
                name=name.strip(),
                root=str(root),
                input_video=str(copied_video.relative_to(root)),
                original_video=str(video_path),
                source_language=source_language,
                target_language=target_language,
                created_at=now,
                updated_at=now,
            )
            project.save()
            return project
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @staticmethod
    def load(manifest: str | Path) -> VideoProject:
        manifest_path = Path(manifest).expanduser().resolve()
        if manifest_path.is_dir():
            manifest_path = manifest_path / PROJECT_FILE
        if not manifest_path.is_file():
            raise ValueError("Không tìm thấy project.json.")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["root"] = str(manifest_path.parent)
        project = VideoProject(**data)
        for folder in PROJECT_FOLDERS:
            project.path(folder).mkdir(exist_ok=True)
        return project
