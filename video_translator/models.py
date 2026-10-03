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
class StepResult:
    step: StepId
    summary: str
    artifacts: dict[str, str] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

