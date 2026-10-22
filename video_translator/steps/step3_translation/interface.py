from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ...common.models import Segment

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class TranslationResponse:
    translations: dict[int, str]
    credential_source: str = ""


@dataclass(frozen=True, slots=True)
class DialogueAnalysisResponse:
    profile: dict[str, object]
    credential_source: str = ""


@dataclass(frozen=True, slots=True)
class TranslationReviewResponse:
    corrections: dict[int, str]
    issues: list[dict[str, object]]
    credential_source: str = ""


class TranslationProvider(Protocol):
    """Hợp đồng trung lập cho Gemini và các provider extension trong tương lai."""

    def analyze_dialogue(
        self,
        segments: list[Segment],
        proper_name_policy: dict[str, object],
    ) -> DialogueAnalysisResponse: ...

    def translate_batch(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        context_before: list[Segment],
        context_after: list[Segment],
        previous_translations: dict[int, str],
        proper_name_policy: dict[str, object],
        response_file: bool = False,
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> TranslationResponse: ...

    def review_translation(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        proper_name_policy: dict[str, object],
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> TranslationReviewResponse: ...

    def rewrite_for_timing(
        self,
        items: list[dict[str, object]],
        batch_size: int = 30,
        progress: ProgressCallback | None = None,
    ) -> TranslationResponse: ...


TranslationProviderFactory = Callable[[str, str, str, dict[str, Any]], TranslationProvider]
_PROVIDER_FACTORIES: dict[str, TranslationProviderFactory] = {}


def register_translation_provider(name: str, factory: TranslationProviderFactory) -> None:
    """Đăng ký adapter; extension có thể gọi hàm này khi được nạp vào ứng dụng."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("Tên translation provider không được để trống.")
    _PROVIDER_FACTORIES[normalized] = factory


def create_translation_provider(
    name: str,
    model_name: str,
    source_language: str,
    target_language: str,
    options: dict[str, Any] | None = None,
) -> TranslationProvider:
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise RuntimeError(f"Translation provider chưa được đăng ký: {name}")
    return factory(model_name, source_language, target_language, options or {})


def registered_translation_providers() -> tuple[str, ...]:
    return tuple(_PROVIDER_FACTORIES)


