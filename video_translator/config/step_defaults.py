"""Single source of defaults for project settings in Steps 1–7.

Only user-configurable settings belong here. Parsing sentinels and internal
algorithm thresholds stay with the component that owns them.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..common.models import StepId
from .deepseek import DEEPSEEK_MODEL, DEEPSEEK_PROVIDER_NAME
from .gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from .opencode_go import OPENCODE_GO_MODEL, OPENCODE_GO_PROVIDER_NAME
from .prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID
from .translation import PROPER_NAME_MODE_AUTO
from .web_translation import CHATGPT_WEB_PROVIDER_NAME, CLAUDE_WEB_PROVIDER_NAME, WEB_CURRENT_MODEL
from .tts import (
    EDGE_TTS_PROVIDER, EDGE_VOICES, MELO_OPENVOICE_PROVIDER, MELO_SPEAKERS,
    PIPER_PROVIDER, VIENEU_PROVIDER, VIENEU_VOICES,
)


STEP_DEFAULTS: Mapping[StepId, Mapping[str, Any]] = {
    StepId.EXTRACT: {
        "provider": "MDX-Net — Audio Separator", "ffmpeg_path": "Auto",
        "model": "UVR-MDX-NET-Inst_HQ_4.onnx", "sample_rate": "44.1 kHz",
        "channels": "Stereo", "device": "Auto",
    },
    StepId.STT: {
        "provider": "Stable Whisper", "model": "medium", "device": "Auto",
        "segmentation_mode": "Whisper", "vad_recovery": False,
        "diarization": False, "merge_contained_adjacent_segments": False,
    },
    StepId.TRANSLATE: {
        "provider": GEMINI_PROVIDER_NAME, "model": GEMINI_DEFAULT_MODEL,
        "prompt_template": DEFAULT_PROMPT_TEMPLATE_ID, "batch_size": 250,
        "proper_name_mode": PROPER_NAME_MODE_AUTO,
        "all_project_segments": True, "project_url": "",
        "auto_adjacent_cleanup": True,
    },
    StepId.TTS: {
        "provider": VIENEU_PROVIDER, "single_voice": True,
        "deep_validation": False, "tts_auto_normalize": True,
        "tts_gain_db": 2.0, "voice_consent": True, "speed": 1.0,
        "device": "Auto", "reference_voice": "",
        "speaker_profiles": {}, "speaker_group_profiles": {},
        "speaker_profile_assignments": {}, "shared_speaker_profile": {},
    },
    StepId.SYNC: {
        "provider": "FFmpeg — Duration Sync", "max_speed": 1.5,
        "use_gap": True, "trim_silence": False,
        "auto_extend_video": True, "video_extension_mode": "Hybrid",
        "min_video_speed": 0.8, "max_freeze_duration": 0.2,
    },
    StepId.BUILD_AUDIO: {"provider": "FFmpeg Voice Timeline"},
    StepId.RENDER: {
        "provider": "FFmpeg Renderer", "include_video": True,
        "include_voice": True, "voice_volume": 1.0,
        "include_original_voice": True, "original_voice_volume": 0.2,
        "include_background": True, "background_volume": 0.8,
        "subtitle": False, "burn_subtitle": False,
    },
}

PROVIDER_DEFAULTS: Mapping[tuple[StepId, str], Mapping[str, Any]] = {
    (StepId.TRANSLATE, DEEPSEEK_PROVIDER_NAME): {"model": DEEPSEEK_MODEL},
    (StepId.TRANSLATE, OPENCODE_GO_PROVIDER_NAME): {"model": OPENCODE_GO_MODEL},
    (StepId.TRANSLATE, CHATGPT_WEB_PROVIDER_NAME): {"model": WEB_CURRENT_MODEL},
    (StepId.TRANSLATE, CLAUDE_WEB_PROVIDER_NAME): {"model": WEB_CURRENT_MODEL},
    (StepId.TTS, VIENEU_PROVIDER): {"voice": VIENEU_VOICES[0]},
    (StepId.TTS, MELO_OPENVOICE_PROVIDER): {"voice": MELO_SPEAKERS[0]},
    (StepId.TTS, EDGE_TTS_PROVIDER): {"voice": EDGE_VOICES[0]},
    (StepId.TTS, PIPER_PROVIDER): {
        "voice": "", "model_path": "", "device": "CPU", "length_scale": 1.0,
        "noise_scale": 0.667, "noise_w_scale": 0.8,
        "volume": 1.0, "normalize_audio": True, "piper_speaker_id": 0,
    },
}


def defaults_for(step: StepId, provider: str = "") -> dict[str, Any]:
    """Create an independent settings dictionary for a step/provider."""
    values = deepcopy(dict(STEP_DEFAULTS[step]))
    values.update(deepcopy(dict(PROVIDER_DEFAULTS.get((step, provider), {}))))
    return values


def default_value(step: StepId, key: str, provider: str = "") -> Any:
    values = defaults_for(step, provider)
    return values[key]


def with_defaults(step: StepId, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Apply project settings over registry defaults without mutating either."""
    provider = str(settings.get("provider", STEP_DEFAULTS[step]["provider"]))
    values = defaults_for(step, provider)
    values.update(settings)
    return values
