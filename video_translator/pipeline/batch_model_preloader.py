from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..steps.step2_transcription.service import preload_batch_models
from ..steps.step4_tts.service import TextToSpeechService
from ..steps.step4_tts.providers import provider_id


class BatchModelPreloader(QThread):
    progress_changed = Signal(str)
    loaded = Signal(object)

    def __init__(
        self, session_key: str, stt_settings: dict[str, object],
        tts_settings: dict[str, object], target_language: str,
    ) -> None:
        super().__init__()
        self.session_key = session_key
        self.stt_settings = dict(stt_settings)
        self.tts_settings = dict(tts_settings)
        self.target_language = target_language

    def run(self) -> None:
        errors: list[str] = []
        if self.stt_settings:
            self.progress_changed.emit("Đang nạp model Step 2…")
            try:
                preload_batch_models(self.session_key, self.stt_settings)
            except Exception as exc:
                errors.append(f"Step 2: {exc}")
        if self.tts_settings and self.tts_settings.get("provider") != provider_id("edge"):
            self.progress_changed.emit("Đang nạp model Step 4…")
            try:
                settings = dict(self.tts_settings)
                provider = str(settings.get("provider", ""))
                TextToSpeechService(provider, settings).preload(
                    self.session_key, self.target_language,
                )
            except Exception as exc:
                errors.append(f"Step 4: {exc}")
        self.loaded.emit(errors)
