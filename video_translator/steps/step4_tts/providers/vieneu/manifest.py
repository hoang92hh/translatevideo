from .config import LICENSE_NOTE, PROVIDER_ID, VERSION
from ..manifest import TtsProviderManifest

MANIFEST = TtsProviderManifest(
    id=PROVIDER_ID,
    name="VieNeu-TTS",
    version=VERSION,
    component_path="video_translator.steps.step4_tts.providers.vieneu.ui:VieNeuTtsComponent",
    description="TTS tiếng Việt local; hỗ trợ preset hoặc audio tham chiếu theo speaker.",
    license_note=LICENSE_NOTE,
)

