from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...config.tts import (
    MELO_LANGUAGE_CODES,
    MELO_OPENVOICE_PROVIDER,
    PROVIDER_LICENSE_NOTES,
    VIENEU_PROVIDER,
)
from ...errors import UserFacingError
from ...models import StepId, StepResult, TtsCandidate
from ...services import TextToSpeechService
from ...state import ProjectState
from .common import previous_segments


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", VIENEU_PROVIDER))
    if provider == VIENEU_PROVIDER and state.target_language != "Vietnamese":
        raise UserFacingError(
            "VieNeu-TTS không phù hợp ngôn ngữ đích",
            f"Project đang dùng ngôn ngữ đích {state.target_language}; VieNeu-TTS dành cho tiếng Việt.",
            "Chọn MeloTTS/OpenVoice hoặc Edge TTS cho ngôn ngữ này.",
        )
    if provider == MELO_OPENVOICE_PROVIDER and state.target_language not in MELO_LANGUAGE_CODES:
        raise UserFacingError(
            "MeloTTS không hỗ trợ ngôn ngữ đích",
            f"MeloTTS không có model cho {state.target_language}.",
            "MeloTTS hỗ trợ English, Spanish, Chinese, Japanese và Korean.",
        )
    reference = str(settings.get("reference_voice", "")).strip()
    raw_profiles = settings.get("speaker_profiles", {})
    speaker_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    raw_shared_profile = settings.get("shared_speaker_profile", {})
    shared_profile = raw_shared_profile if isinstance(raw_shared_profile, dict) else {}
    references = [reference]
    references.extend(
        str(profile.get("reference_voice", "")).strip()
        for profile in speaker_profiles.values()
        if isinstance(profile, dict)
    )
    references.append(str(shared_profile.get("reference_voice", "")).strip())
    references = [item for item in references if item]
    missing_references = [item for item in references if not Path(item).is_file()]
    if missing_references:
        raise UserFacingError(
            "Không tìm thấy file giọng tham chiếu",
            missing_references[0],
            "Chọn lại file audio tham chiếu còn tồn tại.",
        )
    if references and not bool(settings.get("voice_consent", False)):
        raise UserFacingError(
            "Chưa xác nhận quyền sử dụng giọng",
            "Bạn phải xác nhận có quyền hoặc sự đồng ý của chủ giọng trước khi clone/chuyển giọng.",
            "Đánh dấu ô xác nhận quyền sử dụng giọng rồi chạy lại.",
        )

    segments = previous_segments(state, StepId.TTS)
    if not segments or any(not segment.translated_text.strip() for segment in segments):
        raise UserFacingError(
            "Bản dịch đầu vào không hợp lệ",
            "Step 4 cần tất cả segment có nội dung translated_text.",
            "Chọn lại một output hợp lệ từ Step 3.",
        )
    if provider in {VIENEU_PROVIDER, MELO_OPENVOICE_PROVIDER} and (speaker_profiles or shared_profile):
        missing_speakers: list[str] = []
        for speaker_id in dict.fromkeys(
            segment.speaker_id.strip() or "SPEAKER_UNKNOWN" for segment in segments
        ):
            profile = (
                speaker_profiles.get(speaker_id, {})
                if speaker_id in {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}
                else shared_profile
            )
            profile_reference = (
                str(profile.get("reference_voice", "")).strip()
                if isinstance(profile, dict)
                else ""
            )
            if not profile_reference and not reference:
                missing_speakers.append(speaker_id)
        if missing_speakers:
            raise UserFacingError(
                "Chưa chọn đủ giọng tham chiếu",
                "Thiếu giọng cho: " + ", ".join(missing_speakers),
                "Chọn reference riêng cho SPEAKER_00–02 và reference chung nếu có speaker còn lại.",
            )
    now = datetime.now(timezone.utc)
    candidate_id = f"tts-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    candidate_folder = Path(state.workspace_path("generated_audio", candidate_id))
    segment_folder = candidate_folder / "segments"
    manifest_path = candidate_folder / "manifest.json"
    segment_folder.mkdir(parents=True, exist_ok=False)
    try:
        synthesis = TextToSpeechService(provider, settings).synthesize(
            [
                {
                    "id": segment.id,
                    "text": segment.translated_text,
                    "speaker_id": segment.speaker_id,
                }
                for segment in segments
            ],
            segment_folder,
            state.target_language,
            progress,
        )
        for segment, audio_file, segment_result in zip(
            segments, synthesis.files, synthesis.segments, strict=True
        ):
            segment.audio_file = audio_file
            segment.tts_provider = segment_result.provider
            segment.tts_model = segment_result.model
            segment.tts_voice = segment_result.voice
            segment.tts_reference_voice = segment_result.reference_voice
            segment.tts_actual_device = segment_result.actual_device
        resolved_profiles: dict[str, dict[str, str]] = {}
        for segment, segment_result in zip(segments, synthesis.segments, strict=True):
            speaker_id = segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
            resolved_profiles[speaker_id] = {
                "provider": segment_result.provider,
                "model": segment_result.model,
                "voice": segment_result.voice,
                "reference_voice": segment_result.reference_voice,
                "actual_device": segment_result.actual_device,
            }
        metadata = {
            "provider": provider,
            "model": synthesis.model,
            "voice": synthesis.voice,
            "speed": float(settings.get("speed", 1.0)),
            "requested_device": synthesis.requested_device,
            "actual_device": synthesis.actual_device,
            "reference_voice": reference,
            "speaker_profiles": resolved_profiles,
            "requested_speaker_profiles": speaker_profiles,
            "shared_speaker_profile": shared_profile,
            "voice_rights_confirmed": bool(settings.get("voice_consent", False)),
            "license_note": PROVIDER_LICENSE_NOTES.get(provider, ""),
            "source_translation_candidate_id": state.selected_translation_candidate_id,
            "tts_candidate_id": candidate_id,
            "recommended_tts_candidate_id": candidate_id,
        }
        payload = {
            "version": 2,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            **metadata,
            "segments": [
                {
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "speaker_id": segment.speaker_id,
                    "merge_parts": segment.merge_parts,
                    "source_text": segment.source_text,
                    "translated_text": segment.translated_text,
                    "audio_file": segment.audio_file,
                    "tts_provider": segment.tts_provider,
                    "tts_model": segment.tts_model,
                    "tts_voice": segment.tts_voice,
                    "tts_reference_voice": segment.tts_reference_voice,
                    "tts_actual_device": segment.tts_actual_device,
                }
                for segment in segments
            ],
        }
        temporary = manifest_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
    except Exception:
        shutil.rmtree(candidate_folder, ignore_errors=True)
        raise

    summary = f"Đã tạo {len(segments)} file giọng nói · {synthesis.model} · {synthesis.actual_device}"
    candidate = TtsCandidate(
        id=candidate_id,
        label=f"{synthesis.model} · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(manifest_path),
        folder=str(candidate_folder),
        provider=provider,
        voice=synthesis.voice,
        segment_count=len(segments),
        summary=summary,
        metadata=metadata,
    )
    return StepResult(
        step=StepId.TTS,
        summary=summary,
        artifacts={"tts_manifest": str(manifest_path), "audio_folder": str(segment_folder)},
        segments=segments,
        metadata=metadata,
        tts_candidates=[candidate],
    )
