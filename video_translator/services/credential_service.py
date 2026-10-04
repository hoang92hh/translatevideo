from __future__ import annotations

import os
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

from ..errors import UserFacingError


SERVICE_NAME = "TransLanguage"
GOOGLE_GEMINI = "google-gemini"


@dataclass(frozen=True, slots=True)
class CredentialInfo:
    provider_id: str
    configured: bool
    source: str = ""
    value: str = ""


class CredentialService:
    ENVIRONMENT_KEYS = {GOOGLE_GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY")}

    @classmethod
    def get(cls, provider_id: str, include_value: bool = True) -> CredentialInfo:
        keyring_error: KeyringError | None = None
        try:
            stored = keyring.get_password(SERVICE_NAME, provider_id)
        except KeyringError as exc:
            stored = None
            keyring_error = exc
        if stored:
            return CredentialInfo(
                provider_id, True, "Windows Credential Locker", stored if include_value else ""
            )
        for variable in cls.ENVIRONMENT_KEYS.get(provider_id, ()):
            value = os.environ.get(variable, "").strip()
            if value:
                return CredentialInfo(
                    provider_id, True, f"Biến môi trường {variable}", value if include_value else ""
                )
        if keyring_error:
            raise UserFacingError(
                "Không thể đọc kho credential",
                "Windows Credential Locker không thể cung cấp API key.",
                "Kiểm tra tài khoản Windows và dịch vụ Credential Manager rồi thử lại.",
                repr(keyring_error),
            ) from keyring_error
        return CredentialInfo(provider_id, False)

    @staticmethod
    def save(provider_id: str, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise UserFacingError(
                "API key trống",
                "Không thể lưu API key trống.",
                "Dán API key của provider rồi thử lại.",
            )
        try:
            keyring.set_password(SERVICE_NAME, provider_id, secret)
        except KeyringError as exc:
            raise UserFacingError(
                "Không thể lưu credential",
                "API key không thể lưu vào Windows Credential Locker.",
                "Kiểm tra tài khoản Windows và dịch vụ Credential Manager rồi thử lại.",
                repr(exc),
            ) from exc

    @staticmethod
    def delete(provider_id: str) -> None:
        try:
            if keyring.get_password(SERVICE_NAME, provider_id):
                keyring.delete_password(SERVICE_NAME, provider_id)
        except KeyringError as exc:
            raise UserFacingError(
                "Không thể xóa credential",
                "API key không thể xóa khỏi Windows Credential Locker.",
                "Kiểm tra tài khoản Windows và dịch vụ Credential Manager rồi thử lại.",
                repr(exc),
            ) from exc
