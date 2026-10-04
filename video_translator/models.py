from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepId(str, Enum):
    EXTRACT = "extract"
    STT = "stt"
    TRANSLATE = "translate"
    TTS = "tts"
    SYNC = "sync"
    BUILD_AUDIO = "build_audio"
    RENDER = "render"


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    STALE = "stale"
    ERROR = "error"


STEP_ORDER = [
    StepId.EXTRACT,
    StepId.STT,
    StepId.TRANSLATE,
    StepId.TTS,
    StepId.SYNC,
    StepId.BUILD_AUDIO,
    StepId.RENDER,
]


@dataclass(slots=True)
class Segment:
    id: int
    start: float
    end: float
    source_text: str = ""
    translated_text: str = ""
    audio_file: str = ""
    synced_audio_file: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
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
        return self.stems.get(stem, "")


@dataclass(slots=True)
class TranscriptCandidate:
    id: str
    label: str
    created_at: str
    path: str
    segment_count: int = 0
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepResult:
    step: StepId
    summary: str
    artifacts: dict[str, str] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    audio_candidates: list[AudioCandidate] = field(default_factory=list)
    transcript_candidates: list[TranscriptCandidate] = field(default_factory=list)
