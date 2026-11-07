from .config import LICENSE_NOTE, PROVIDER_ID, VERSION
from ..manifest import TtsProviderManifest

MANIFEST = TtsProviderManifest(
    id=PROVIDER_ID,
    name="Edge TTS",
    version=VERSION,
    component_path="video_translator.steps.step4_tts.providers.edge.ui:EdgeTtsComponent",
    description="Dịch vụ TTS online.",
    license_note=LICENSE_NOTE,
)

