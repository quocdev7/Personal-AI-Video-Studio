"""Tác vụ "Kiểm tra media": đọc thông tin file bằng ffprobe và phát hiện vấn đề kỹ thuật cơ bản.

Đây là nền cho Module G (kiểm tra trước khi đăng); các module khác cũng dùng `probe` để
kiểm tra đầu vào trước khi xử lý.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pas.core.errors import InvalidInput
from pas.jobs.context import JobContext
from pas.jobs.registry import TaskRegistry, TaskSpec
from pas.media.ffmpeg import FFmpegTools, MediaInfo, probe

KIND = "media.inspect"

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}


def validate(params: dict) -> None:
    raw = params.get("path")
    if not raw:
        raise InvalidInput("Chưa chọn file.", "Bấm 'Chọn file' hoặc kéo thả file vào cửa sổ.")
    path = Path(raw)
    if not path.is_file():
        raise InvalidInput(f"Không tìm thấy file: {path}", "Kiểm tra lại đường dẫn.")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidInput(
            f"Định dạng '{path.suffix or '(không có đuôi)'}' chưa được hỗ trợ.",
            "Các định dạng hỗ trợ: " + ", ".join(sorted(e.lstrip('.').upper() for e in SUPPORTED_EXTENSIONS)),
        )


def find_issues(info: MediaInfo) -> list[str]:
    issues: list[str] = []
    if info.duration <= 0:
        issues.append("Không xác định được thời lượng — file có thể ghi dở hoặc bị hỏng.")
    if not info.has_audio:
        issues.append("File không có âm thanh — không tạo được phụ đề từ lời nói.")
    for v in info.video:
        if v.width <= 0 or v.height <= 0:
            issues.append(f"Luồng video #{v.index} không có kích thước hợp lệ.")
        if v.fps <= 0:
            issues.append(f"Luồng video #{v.index} không xác định được FPS.")
        elif v.fps > 120:
            issues.append(f"Luồng video #{v.index} có FPS bất thường ({v.fps}).")
    return issues


def aspect_label(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "?"
    ratio = width / height
    for label, value in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0), ("4:3", 4 / 3), ("3:4", 3 / 4), ("21:9", 21 / 9)):
        if abs(ratio - value) / value < 0.02:
            return label
    return f"{ratio:.2f}:1"


def make_handler(tools_provider: Callable[[], FFmpegTools]):
    def run(ctx: JobContext) -> dict:
        path = Path(ctx.params["path"])
        ctx.report(0.05, "Đang tìm FFmpeg")
        tools = tools_provider()
        ctx.checkpoint()
        ctx.report(0.2, f"Đang đọc {path.name}")
        info = probe(path, tools)
        ctx.checkpoint()
        issues = find_issues(info)
        ctx.log(f"Thời lượng {info.duration:.2f}s, {len(info.video)} video, {len(info.audio)} audio, {len(issues)} vấn đề")
        result = info.to_dict()
        result["issues"] = issues
        result["aspect"] = aspect_label(info.video[0].width, info.video[0].height) if info.video else ""
        result["output_dir"] = str(path.parent)
        return result

    return run


def register(registry: TaskRegistry, tools_provider: Callable[[], FFmpegTools]) -> None:
    registry.register(TaskSpec(KIND, "Kiểm tra media", make_handler(tools_provider), validate))
