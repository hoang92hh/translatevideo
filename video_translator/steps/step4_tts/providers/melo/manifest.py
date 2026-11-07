from .config import (
    LICENSE_NOTE,
    PROVIDER_ID,
    RUNTIME_ENV,
    RUNTIME_NAME,
    RUNTIME_SETUP_SCRIPT,
    VERSION,
)
from ..manifest import TtsProviderManifest

MANIFEST = TtsProviderManifest(
    id=PROVIDER_ID,
    name="MeloTTS + OpenVoice V2",
    version=VERSION,
    component_path="video_translator.steps.step4_tts.providers.melo.ui:MeloOpenVoiceTtsComponent",
    description="MeloTTS tạo giọng nền và OpenVoice chuyển màu giọng.",
    license_note=LICENSE_NOTE,
    runtime_name=RUNTIME_NAME,
    runtime_env=RUNTIME_ENV,
    runtime_setup_script=RUNTIME_SETUP_SCRIPT,
    runtime_description="MeloTTS/OpenVoice",
)

