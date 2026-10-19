from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pas.core.db import Database  # noqa: E402
from pas.media.ffmpeg import CREATE_NO_WINDOW, FFmpegTools, find_binary  # noqa: E402

# Thư mục có dấu tiếng Việt, chữ Hán và khoảng trắng: trường hợp dễ vỡ nhất trên Windows.
UNICODE_DIR = "Thư mục thử nghiệm 中文 & (1)"


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Dữ liệu ứng dụng"
    d.mkdir()
    return d


def _tools() -> FFmpegTools | None:
    ffmpeg, ffprobe = find_binary("ffmpeg"), find_binary("ffprobe")
    return FFmpegTools(ffmpeg, ffprobe) if ffmpeg and ffprobe else None


@pytest.fixture(scope="session")
def ffmpeg_tools() -> FFmpegTools:
    tools = _tools()
    if tools is None:
        pytest.skip("Không tìm thấy FFmpeg — bỏ qua test cần FFmpeg")
    return tools


def make_media(tools: FFmpegTools, path: Path, seconds: float = 2.0, video: bool = True, audio: bool = True,
               size: str = "320x180", silent: bool = False) -> Path:
    """Sinh file thử bằng nguồn tổng hợp của FFmpeg (không cần tải gì)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        args += ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        src = f"anullsrc=r=44100:cl=mono" if silent else f"sine=frequency=440:sample_rate=44100:duration={seconds}"
        args += ["-f", "lavfi", "-i", src]
    if video:
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    args += ["-t", str(seconds), str(path)]
    subprocess.run(args, check=True, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
    return path
