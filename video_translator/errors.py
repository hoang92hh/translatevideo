from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    title: str
    message: str
    suggestion: str = ""
    technical_detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "technical_detail": self.technical_detail,
        }


class UserFacingError(RuntimeError):
    def __init__(
        self,
        title: str,
        message: str,
        suggestion: str = "",
        technical_detail: str = "",
    ) -> None:
        super().__init__(message)
        self.error_message = ErrorMessage(title, message, suggestion, technical_detail)


def error_payload(error: Exception) -> dict[str, str]:
    if isinstance(error, UserFacingError):
        return error.error_message.as_dict()
    if isinstance(error, ModuleNotFoundError):
        module = error.name or "không xác định"
        return ErrorMessage(
            "Thiếu thư viện Python",
            f"Không tìm thấy module “{module}”.",
            "Cài lại dependency bằng đúng Python đang chạy ứng dụng: python -m pip install -e .",
            repr(error),
        ).as_dict()
    if isinstance(error, PermissionError):
        return ErrorMessage(
            "Không có quyền truy cập file",
            "Một file đầu vào hoặc đầu ra đang bị khóa hoặc không có quyền ghi.",
            "Đóng ứng dụng đang mở file, kiểm tra quyền thư mục rồi thử lại.",
            repr(error),
        ).as_dict()
    if isinstance(error, FileNotFoundError):
        missing = error.filename or "file/executable không xác định"
        return ErrorMessage(
            "Không tìm thấy file cần thiết",
            f"Không tìm thấy: {missing}",
            "Kiểm tra đường dẫn cấu hình và thử lại.",
            repr(error),
        ).as_dict()
    if isinstance(error, MemoryError) or "out of memory" in str(error).lower():
        return ErrorMessage(
            "Không đủ bộ nhớ",
            "Provider không đủ RAM hoặc VRAM để hoàn thành tác vụ.",
            "Đóng bớt ứng dụng, dùng CPU hoặc chọn model nhẹ hơn.",
            repr(error),
        ).as_dict()
    return ErrorMessage(
        "Không thể hoàn thành step",
        str(error) or "Đã xảy ra lỗi chưa xác định.",
        "Mở phần chi tiết kỹ thuật và gửi nội dung đó khi cần hỗ trợ.",
        repr(error),
    ).as_dict()

