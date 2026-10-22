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
    name="Piper TTS",
    version=VERSION,
    component_path="video_translator.steps.step4_tts.providers.piper.ui:PiperTtsComponent",
    description="TTS local bằng voice model ONNX cố định.",
    license_note=LICENSE_NOTE,
    runtime_name=RUNTIME_NAME,
    runtime_env=RUNTIME_ENV,
    runtime_setup_script=RUNTIME_SETUP_SCRIPT,
    runtime_description="Piper TTS",
)

