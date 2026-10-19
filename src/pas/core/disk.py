"""Kiểm tra dung lượng ổ đĩa trước khi ghi file lớn."""

from __future__ import annotations

import shutil
from pathlib import Path

from pas.core.errors import InsufficientDiskSpace

GB = 1024**3


def _existing_ancestor(path: Path) -> Path:
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    # Ổ đĩa không tồn tại: để shutil.disk_usage báo lỗi gốc.
    return path


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(_existing_ancestor(path)).free


def ensure_free_space(path: Path, required_bytes: int) -> None:
    free = free_bytes(path)
    if free < required_bytes:
        drive = _existing_ancestor(path).anchor or str(path)
        raise InsufficientDiskSpace(
            f"Ổ {drive} chỉ còn {free / GB:.1f} GB, cần khoảng {required_bytes / GB:.1f} GB.",
            "Giải phóng dung lượng hoặc chọn thư mục đầu ra trên ổ khác (Cài đặt hoặc Dự án).",
        )
