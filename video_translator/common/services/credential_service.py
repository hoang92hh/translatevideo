from __future__ import annotations

import os
from dataclasses import dataclass

GOOGLE_GEMINI = "google-gemini"
DEEPSEEK = "deepseek"
OPENCODE_GO = "opencode-go"


@dataclass(frozen=True, slots=True)
class CredentialInfo:
    provider_id: str
    configured: bool
    source: str = ""
    value: str = ""


class CredentialService:
    ENVIRONMENT_KEYS = {
        GOOGLE_GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        DEEPSEEK: ("DEEPSEEK_API_KEY",),
        OPENCODE_GO: ("OPENCODE_API_KEY",),
    }

    @classmethod
    def get(cls, provider_id: str, include_value: bool = True) -> CredentialInfo:
        for variable in cls.ENVIRONMENT_KEYS.get(provider_id, ()):
            value = os.environ.get(variable, "").strip()
            if value:
                return CredentialInfo(
                    provider_id, True, f"Biến môi trường {variable}", value if include_value else ""
                )
        return CredentialInfo(provider_id, False)

