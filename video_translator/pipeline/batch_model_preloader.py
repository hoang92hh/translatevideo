from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..common.models import StepId
from ..steps.step2_transcription.service import preload_batch_models
from ..steps.step4_tts.service import TextToSpeechService
from ..steps.step4_tts.providers import provider_id


class BatchModelPreloader(QThread):
    progress_changed = Signal(str)
    loaded = Signal(object)

    def __init__(
        self, session_key: str, step: StepId,
        settings: dict[str, object], target_language: str,
    ) -> None:
        super().__init__()
        self.session_key = session_key
        self.step = step
        self.settings = dict(settings)
        self.target_language = target_language

    def run(self) -> None:
        errors: list[str] = []
        if self.step == StepId.STT:
            self.progress_changed.emit("Đang nạp model Step 2…")
            try:
                preload_batch_models(self.session_key, self.settings)
            except Exception as exc:
                errors.append(f"Step 2: {exc}")
        elif self.step == StepId.TTS and self.settings.get("provider") != provider_id("edge"):
            self.progress_changed.emit("Đang nạp model Step 4…")
            try:
                settings = dict(self.settings)
                provider = str(settings.get("provider", ""))
                TextToSpeechService(provider, settings).preload(
                    self.session_key, self.target_language,
                )
            except Exception as exc:
                errors.append(f"Step 4: {exc}")
        self.loaded.emit(errors)
