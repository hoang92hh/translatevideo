from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..common.models import StepId
from ..steps.step1_separation.service import AudioSeparatorService
from ..steps.step2_transcription.service import preload_batch_models
from ..steps.step4_tts.service import TextToSpeechService
from ..steps.step4_tts.providers import provider_id


class BatchModelPreloader(QThread):
    progress_changed = Signal(int, str)
    loaded = Signal(object)

    def __init__(
        self, session_key: str, step: StepId,
        settings: dict[str, object], target_language: str,
        source_language: str = "",
    ) -> None:
        super().__init__()
        self.session_key = session_key
        self.step = step
        self.settings = dict(settings)
        self.target_language = target_language
        self.source_language = source_language

    def run(self) -> None:
        errors: list[str] = []
        if self.step == StepId.EXTRACT:
            self.progress_changed.emit(0, "Đang nạp model Step 1…")
            try:
                AudioSeparatorService.preload_batch_model(self.session_key, self.settings)
            except Exception as exc:
                errors.append(f"Step 1: {exc}")
        elif self.step == StepId.STT:
            self.progress_changed.emit(0, "Đang nạp model Step 1.5…")
            try:
                preload_batch_models(
                    self.session_key,
                    {**self.settings, "_source_language": self.source_language},
                )
            except Exception as exc:
                errors.append(f"Step 1.5: {exc}")
        elif self.step == StepId.TTS and self.settings.get("provider") != provider_id("edge"):
            self.progress_changed.emit(0, "Đang nạp model Step 4…")
            try:
                settings = dict(self.settings)
                settings.pop("_batch_model_session", None)
                provider = str(settings.get("provider", ""))
                TextToSpeechService(provider, settings).preload(
                    self.session_key, self.target_language,
                    lambda value, message: self.progress_changed.emit(
                        value, f"Đang nạp model Step 4: {message}"
                    ),
                )
            except Exception as exc:
                errors.append(f"Step 4: {exc}")
        self.loaded.emit(errors)
