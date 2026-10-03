from __future__ import annotations

from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState
from .common import previous_segments


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = previous_segments(state, StepId.TRANSLATE)
    vietnamese = [
        "Xin chào mọi người.",
        "Hôm nay chúng ta thảo luận về vấn đề này.",
        "Cảm ơn mọi người đã quan tâm.",
    ]
    english = [
        "Hello everyone.",
        "Today we are discussing this issue.",
        "Thank you for your attention.",
    ]
    translations = vietnamese if state.target_language == "Vietnamese" else english
    for segment, text in zip(segments, translations, strict=False):
        segment.translated_text = text
    return StepResult(
        step=StepId.TRANSLATE,
        summary=f"Đã mô phỏng dịch {state.source_language} → {state.target_language}.",
        artifacts={"translation": state.workspace_path("translations", "translated_segments.json")},
        segments=segments,
        metadata=settings,
    )

