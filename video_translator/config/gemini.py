from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeminiModelSpec:
    id: str
    description: str
    access_note: str = ""


GEMINI_PROVIDER_NAME = "Google Gemini"
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_CONNECTION_TEST_MODEL = GEMINI_DEFAULT_MODEL

# Cập nhật danh sách này khi Google thêm, đổi tên hoặc ngừng cung cấp model.
GEMINI_MODELS: tuple[GeminiModelSpec, ...] = (
    GeminiModelSpec(
        "gemini-3.5-flash-lite",
        "Khuyên dùng cho dịch thuật: nhanh và tiết kiệm chi phí.",
    ),
    GeminiModelSpec(
        "gemini-3.8-flash",
        "Model Flash mạnh hơn cho tác vụ phức tạp, chi phí cao hơn.",
    ),
    GeminiModelSpec(
        "gemini-2.5-flash",
        "Model Flash thế hệ 2.5.",
        "Legacy: Google giới hạn quyền truy cập đối với project mới.",
    ),
    GeminiModelSpec(
        "gemini-2.5-pro",
        "Model Pro cho tác vụ suy luận phức tạp; thường không cần cho subtitle.",
        "Legacy: Google giới hạn quyền truy cập đối với project mới.",
    ),
)

GEMINI_MODEL_IDS = tuple(model.id for model in GEMINI_MODELS)
GEMINI_MODEL_BY_ID = {model.id: model for model in GEMINI_MODELS}
GEMINI_RECOMMENDED_MODEL_IDS = tuple(
    model.id for model in GEMINI_MODELS if not model.access_note
)


def gemini_model_note(model_id: str) -> str:
    model = GEMINI_MODEL_BY_ID.get(model_id)
    if not model:
        return "Model chưa có mô tả trong cấu hình ứng dụng."
    return " ".join(part for part in (model.description, model.access_note) if part)
