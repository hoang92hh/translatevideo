from ..manifest import SeparationProviderManifest
from .config import AVAILABLE, PROVIDER_ID, VERSION

MANIFEST = SeparationProviderManifest(
    id=PROVIDER_ID,
    name="Demucs",
    version=VERSION,
    available=AVAILABLE,
    requirements_file="requirements.txt",
)
