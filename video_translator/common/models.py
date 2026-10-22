"""Shared value objects used by the video-translation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StepId(str, Enum):
    EXTRACT = "extract"
    STT = "stt"
    TRANSLATE = "translate"
    TTS = "tts"
    SYNC = "sync"
    BUILD_AUDIO = "build_audio"
    RENDER = "render"


STEP_ORDER = (
    StepId.EXTRACT,
    StepId.STT,
    StepId.TRANSLATE,
    StepId.TTS,
    StepId.SYNC,
    StepId.BUILD_AUDIO,
    StepId.RENDER,
)


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    STALE = "stale"
    ERROR = "error"


@dataclass
class Segment:
    id: int
    start: float
    end: float
    speaker_id: str = ""
    merge_parts: list[dict[str, Any]] = field(default_factory=list)
    source_text: str = ""
    translated_text: str = ""
    audio_file: str = ""
    synced_audio_file: str = ""


@dataclass
class AudioCandidate:
    id: str
    label: str
    provider: str
    model_family: str
    model_name: str
    created_at: str
    stems: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def stem_path(self, stem: str) -> str:
        return str(self.stems.get(stem, ""))


@dataclass
class BaseCandidate:
    id: str
    label: str
    created_at: str
    path: str
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptCandidate(BaseCandidate):
    segment_count: int = 0


@dataclass
class TranslationCandidate(BaseCandidate):
    segment_count: int = 0


@dataclass
class TtsCandidate(BaseCandidate):
    folder: str = ""
    provider: str = ""
    voice: str = ""
    segment_count: int = 0


@dataclass
class SyncCandidate(BaseCandidate):
    folder: str = ""
    segment_count: int = 0
    error_count: int = 0


@dataclass
class BuildAudioCandidate(BaseCandidate):
    folder: str = ""
    audio_file: str = ""
    source_sync_candidate_id: str = ""
    duration_seconds: float = 0.0
    segment_count: int = 0


@dataclass
class RenderCandidate(BaseCandidate):
    folder: str = ""
    video_file: str = ""
    subtitle_file: str = ""
    original_voice_used: bool = False
    background_used: bool = False
    burned_subtitle: bool = False
    duration_seconds: float = 0.0


@dataclass
class StepResult:
    step: StepId
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    audio_candidates: list[AudioCandidate] = field(default_factory=list)
    transcript_candidates: list[TranscriptCandidate] = field(default_factory=list)
    translation_candidates: list[TranslationCandidate] = field(default_factory=list)
    tts_candidates: list[TtsCandidate] = field(default_factory=list)
    sync_candidates: list[SyncCandidate] = field(default_factory=list)
    build_audio_candidates: list[BuildAudioCandidate] = field(default_factory=list)
    render_candidates: list[RenderCandidate] = field(default_factory=list)


__all__ = [
    "AudioCandidate",
    "BuildAudioCandidate",
    "RenderCandidate",
    "Segment",
    "STEP_ORDER",
    "StepId",
    "StepResult",
    "StepStatus",
    "SyncCandidate",
    "TranscriptCandidate",
    "TranslationCandidate",
    "TtsCandidate",
]
