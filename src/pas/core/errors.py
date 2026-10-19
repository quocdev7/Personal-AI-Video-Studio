"""Lỗi dành cho người dùng: luôn có thông điệp tiếng Việt và gợi ý xử lý."""

from __future__ import annotations


class PasError(Exception):
    """Lỗi có thể hiển thị cho người dùng.

    message: mô tả ngắn chuyện gì đã xảy ra.
    hint: người dùng nên làm gì tiếp theo.
    """

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message if not self.hint else f"{self.message} — {self.hint}"


class InvalidInput(PasError):
    """Đầu vào không hợp lệ: file thiếu, định dạng không hỗ trợ, tham số sai."""


class DependencyMissing(PasError):
    """Thiếu phần mềm hoặc thư viện cần thiết."""


class InsufficientDiskSpace(PasError):
    """Không đủ dung lượng ổ đĩa."""


class ExternalToolError(PasError):
    """Công cụ ngoài (FFmpeg, ffprobe...) trả về lỗi."""


class JobCancelled(Exception):
    """Tác vụ bị hủy (bởi người dùng hoặc khi tắt ứng dụng). Không phải lỗi."""
