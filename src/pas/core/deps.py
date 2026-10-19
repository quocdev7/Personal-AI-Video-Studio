"""Kiểm tra môi trường: FFmpeg, thư viện tùy chọn, dung lượng. Trả về trạng thái kèm hướng dẫn."""

from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from pas.core.config import Settings
from pas.core.disk import GB, free_bytes
from pas.core.paths import AppPaths
from pas.media.ffmpeg import INSTALL_HINT, find_binary, tool_version


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = True  # False = chỉ cần cho một module chưa dùng tới


OPTIONAL_PACKAGES = [
    # (tên import, mô tả, lệnh cài)
    ("faster_whisper", "faster-whisper — nhận dạng giọng nói (Module B)", "uv sync --extra asr"),
]


def check_environment(settings: Settings, paths: AppPaths) -> list[DependencyStatus]:
    results: list[DependencyStatus] = []

    results.append(DependencyStatus(
        "Python", sys.version_info[:2] == (3, 12),
        f"{platform.python_version()} ({sys.executable})",
        "Dự án được kiểm thử với Python 3.12. Chạy `uv sync` để dùng đúng phiên bản." if sys.version_info[:2] != (3, 12) else "",
    ))

    for name, configured in (("ffmpeg", settings.ffmpeg_path), ("ffprobe", settings.ffprobe_path)):
        binary = find_binary(name, configured)
        if binary is None:
            detail = f"Không tìm thấy (đường dẫn cấu hình: {configured})" if configured else "Không tìm thấy"
            results.append(DependencyStatus(name, False, detail, INSTALL_HINT))
            continue
        try:
            version = tool_version(binary)
        except Exception as exc:  # binary hỏng / bị chặn bởi antivirus
            results.append(DependencyStatus(name, False, f"{binary}: không chạy được ({exc})",
                                            "Cài lại FFmpeg hoặc chọn binary khác trong Cài đặt."))
            continue
        results.append(DependencyStatus(name, True, f"{version} — {binary}"))

    for module, desc, install in OPTIONAL_PACKAGES:
        installed = importlib.util.find_spec(module) is not None
        results.append(DependencyStatus(
            desc, installed, "Đã cài" if installed else "Chưa cài",
            "" if installed else f"Sẽ cài khi triển khai module tương ứng: `{install}`", required=False,
        ))

    min_free = int(settings.min_free_disk_gb * GB)
    locations = [("Dữ liệu ứng dụng", paths.root)]
    if settings.default_output_dir:
        locations.append(("Thư mục đầu ra mặc định", Path(settings.default_output_dir)))
    for label, loc in locations:
        try:
            free = free_bytes(loc)
        except OSError as exc:
            results.append(DependencyStatus(f"Dung lượng — {label}", False, f"{loc}: {exc}",
                                            "Kiểm tra ổ đĩa còn được kết nối."))
            continue
        ok = free >= min_free
        results.append(DependencyStatus(
            f"Dung lượng — {label}", ok, f"{loc} — còn {free / GB:.1f} GB",
            "" if ok else f"Cần tối thiểu {settings.min_free_disk_gb:.1f} GB trống. Giải phóng ổ đĩa hoặc đổi thư mục.",
        ))
    return results
