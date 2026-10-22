"""VieNeu provider boundary.

The worker remains a separate process. This module is the stable provider entry
point used by discovery and future provider-specific runtime code.
"""

from .config import PROVIDER_ID

PROVIDER = PROVIDER_ID
