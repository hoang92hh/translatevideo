from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .project import PROJECT_FOLDERS, VIDEO_PROJECT_FILE, VideoProject


PROJECT_FILE = "project.json"
INPUT_MANIFEST_FILE = ".translanguage-videos.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
VIDEO_STATUSES = {"pending", "processing", "completed", "failed"}
WORK_FOLDERS = tuple(folder for folder in PROJECT_FOLDERS if folder not in {"input", "output"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def folder_key(name: str) -> str:
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_").lower()
    return value or f"video-{uuid4().hex[:8]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@dataclass(slots=True)
class InputVideo:
    path: str
    name: str
    status: str = "pending"
    last_error: str = ""
    project_id: str = ""
    output_path: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ProjectVideo:
    id: str
    name: str
    source_path: str
    workspace: str
    status: str = "pending"
    selected: bool = True
    last_error: str = ""
    output_count: int = 0
    current_step: str = ""
    failed_step: str = ""
    restart_step: str = ""
    last_attempt_at: str = ""


@dataclass(slots=True)
class BatchProject:
    id: str
    name: str
    root: str
    input_folder: str
    source_language: str
    target_language: str
    created_at: str
    updated_at: str
    videos: list[ProjectVideo] = field(default_factory=list)
    batch_settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    merge_outputs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    @property
    def manifest_path(self) -> Path:
        return self.root_path / PROJECT_FILE

    @property
    def input_manifest_path(self) -> Path:
        return Path(self.input_folder) / INPUT_MANIFEST_FILE

    def path(self, *parts: str) -> Path:
        return self.root_path.joinpath(*parts)

    def video(self, video_id: str) -> ProjectVideo | None:
        return next((item for item in self.videos if item.id == video_id), None)

    def save(self) -> None:
        self.updated_at = utc_now()
        write_json(self.manifest_path, asdict(self))


class BatchProjectService:
    @staticmethod
    def scan_input_folder(folder: str | Path) -> list[InputVideo]:
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Thư mục input không tồn tại.")
        stored: dict[str, dict[str, Any]] = {}
        manifest = root / INPUT_MANIFEST_FILE
        if manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                stored = {
                    str(Path(item.get("path", "")).resolve()): item
                    for item in payload.get("videos", [])
                    if item.get("path")
                }
            except (OSError, ValueError, TypeError):
                stored = {}
        videos: list[InputVideo] = []
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            resolved = str(path.resolve())
            previous = stored.get(resolved, {})
            status = str(previous.get("status", "pending"))
            if status not in VIDEO_STATUSES:
                status = "pending"
            videos.append(InputVideo(
                path=resolved,
                name=path.name,
                status=status,
                last_error=str(previous.get("last_error", "")),
                project_id=str(previous.get("project_id", "")),
                output_path=str(previous.get("output_path", "")),
                updated_at=str(previous.get("updated_at", "")),
            ))
        BatchProjectService._save_input_manifest(root, videos)
        return videos

    @staticmethod
    def _save_input_manifest(folder: Path, videos: list[InputVideo]) -> None:
        write_json(folder / INPUT_MANIFEST_FILE, {
            "version": 1,
            "folder": str(folder),
            "updated_at": utc_now(),
            "videos": [asdict(item) for item in videos],
        })

    @staticmethod
    def create(
        name: str,
        input_folder: str,
        selected_videos: list[str],
        parent_folder: str,
        source_language: str,
        target_language: str,
        batch_settings: dict[str, dict[str, Any]] | None = None,
    ) -> BatchProject:
        input_root = Path(input_folder).expanduser().resolve()
        available = {item.path for item in BatchProjectService.scan_input_folder(input_root)}
        selected = [str(Path(path).expanduser().resolve()) for path in selected_videos]
        if not selected:
            raise ValueError("Hãy chọn ít nhất một video cho project.")
        if any(path not in available for path in selected):
            raise ValueError("Danh sách chọn chứa video không thuộc trực tiếp thư mục input.")
        parent = Path(parent_folder).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        root = parent / folder_key(name)
        if root.exists():
            raise FileExistsError(f"Thư mục project đã tồn tại: {root}")
        root.mkdir()
        (root / "output").mkdir()
        records: list[ProjectVideo] = []
        for source in selected:
            path = Path(source)
            key = folder_key(path.name)
            if (root / key).exists():
                key = f"{key}-{uuid4().hex[:6]}"
            (root / key).mkdir()
            records.append(ProjectVideo(
                id=uuid4().hex,
                name=path.name,
                source_path=source,
                workspace=key,
            ))
        now = utc_now()
        project = BatchProject(
            id=uuid4().hex,
            name=name.strip(),
            root=str(root),
            input_folder=str(input_root),
            source_language=source_language,
            target_language=target_language,
            created_at=now,
            updated_at=now,
            videos=records,
            batch_settings=dict(batch_settings or {}),
        )
        for record in records:
            BatchProjectService._create_video_project(project, record)
        project.save()
        return project

    @staticmethod
    def load(manifest: str | Path) -> BatchProject:
        path = Path(manifest).expanduser().resolve()
        if path.is_dir():
            path = path / PROJECT_FILE
        if not path.is_file():
            raise ValueError("Không tìm thấy project.json.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "input_folder" not in data or "videos" not in data:
            raise ValueError("Project một-video cũ không còn được hỗ trợ.")
        data["root"] = str(path.parent)
        data["videos"] = [ProjectVideo(**item) for item in data.get("videos", [])]
        project = BatchProject(**data)
        project.path("output").mkdir(exist_ok=True)
        normalized = False
        for video in project.videos:
            output_count = len(BatchProjectService.render_outputs(project, video.id))
            if video.output_count != output_count:
                video.output_count = output_count
                normalized = True
            if output_count > 0 and video.status == "pending":
                video.status = "completed"
                video.current_step = ""
                video.failed_step = ""
                video.restart_step = ""
                video.last_error = ""
                normalized = True
        if normalized:
            project.save()
        return project

    @staticmethod
    def _create_video_project(batch: BatchProject, record: ProjectVideo) -> VideoProject:
        root = batch.path(record.workspace)
        root.mkdir(parents=True, exist_ok=True)
        for folder in WORK_FOLDERS:
            (root / folder).mkdir(exist_ok=True)
        output_root = batch.path("output", record.workspace)
        output_root.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        project = VideoProject(
            id=record.id,
            name=record.name,
            root=str(root),
            input_video=record.source_path,
            original_video=record.source_path,
            source_language=batch.source_language,
            target_language=batch.target_language,
            created_at=now,
            updated_at=now,
            parent_project_id=batch.id,
            video_id=record.id,
            output_root=str(output_root),
        )
        project.save()
        return project

    @staticmethod
    def add_videos(batch: BatchProject, selected_paths: list[str]) -> list[ProjectVideo]:
        """Add newly selected input videos while preserving existing workspaces."""
        known = {str(Path(video.source_path).resolve()) for video in batch.videos}
        available = {
            item.path
            for item in BatchProjectService.scan_input_folder(batch.input_folder)
        }
        added: list[ProjectVideo] = []
        for value in selected_paths:
            source = str(Path(value).expanduser().resolve())
            if source in known:
                continue
            if source not in available:
                raise ValueError("Video được chọn không thuộc trực tiếp thư mục input.")
            path = Path(source)
            key = folder_key(path.name)
            if batch.path(key).exists() or batch.path("output", key).exists():
                key = f"{key}-{uuid4().hex[:6]}"
            record = ProjectVideo(
                id=uuid4().hex,
                name=path.name,
                source_path=source,
                workspace=key,
            )
            batch.videos.append(record)
            known.add(source)
            BatchProjectService._create_video_project(batch, record)
            added.append(record)
        if added:
            batch.save()
        return added

    @staticmethod
    def open_video(batch: BatchProject, video_id: str) -> VideoProject:
        record = batch.video(video_id)
        if record is None:
            raise ValueError("Video không thuộc project.")
        manifest = batch.path(record.workspace, VIDEO_PROJECT_FILE)
        if not manifest.is_file():
            return BatchProjectService._create_video_project(batch, record)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["root"] = str(manifest.parent)
        data["input_video"] = record.source_path
        data["original_video"] = record.source_path
        data["source_language"] = batch.source_language
        data["target_language"] = batch.target_language
        data["output_root"] = str(batch.path("output", record.workspace))
        project = VideoProject(**data)
        for folder in WORK_FOLDERS:
            project.path(folder).mkdir(exist_ok=True)
        Path(project.output_root).mkdir(parents=True, exist_ok=True)
        return project

    @staticmethod
    def update_video_status(
        batch: BatchProject,
        video_id: str,
        status: str,
        error: str = "",
        output_path: str = "",
        step: str = "",
    ) -> None:
        if status not in VIDEO_STATUSES:
            raise ValueError(f"Trạng thái video không hợp lệ: {status}")
        record = batch.video(video_id)
        if record is None:
            return
        record.status = status
        record.last_error = error
        if status == "processing":
            record.current_step = step
            record.failed_step = ""
            record.last_attempt_at = utc_now()
        elif status == "failed":
            record.current_step = step
            record.failed_step = step
            record.last_attempt_at = utc_now()
        elif status == "completed":
            record.current_step = ""
            record.failed_step = ""
            record.restart_step = ""
            record.last_attempt_at = utc_now()
        elif status == "pending":
            record.current_step = record.restart_step
            record.failed_step = ""
        if status == "completed":
            record.output_count = len(BatchProjectService.render_outputs(batch, video_id))
        batch.save()
        videos = BatchProjectService.scan_input_folder(batch.input_folder)
        source = str(Path(record.source_path).resolve())
        for item in videos:
            if str(Path(item.path).resolve()) == source:
                item.status = status
                item.last_error = error
                item.project_id = batch.id
                item.output_path = output_path
                item.updated_at = utc_now()
                break
        BatchProjectService._save_input_manifest(Path(batch.input_folder).resolve(), videos)

    @staticmethod
    def render_outputs(batch: BatchProject, video_id: str) -> list[dict[str, str]]:
        record = batch.video(video_id)
        if record is None:
            return []
        root = batch.path("output", record.workspace)
        outputs: list[dict[str, str]] = []
        if not root.is_dir():
            return outputs
        for manifest in sorted(root.glob("*/manifest.json"), reverse=True):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            video_file = str(payload.get("output_path", ""))
            if video_file and Path(video_file).is_file():
                outputs.append({
                    "id": str(payload.get("candidate_id", manifest.parent.name)),
                    "video_file": video_file,
                    "manifest": str(manifest),
                    "created_at": str(payload.get("created_at", "")),
                })
        return outputs
