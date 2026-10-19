"""Bọc FFmpeg/ffprobe: tìm binary, đọc thông tin media, chạy lệnh có tiến trình và hủy được.

Mọi lệnh truyền dạng danh sách đối số (không qua shell) nên an toàn với đường dẫn
có dấu tiếng Việt, khoảng trắng và ký tự đặc biệt.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput, JobCancelled

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

INSTALL_HINT = (
    "Cài FFmpeg bằng lệnh `winget install Gyan.FFmpeg` rồi khởi động lại ứng dụng, "
    "hoặc chỉ định đường dẫn ffmpeg.exe / ffprobe.exe trong Cài đặt."
)


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # winget thêm alias ở đây; tiến trình mở trước khi cài sẽ chưa thấy PATH mới.
        dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
    project_root = Path(__file__).resolve().parents[3]
    dirs.append(project_root / "tools" / "ffmpeg" / "bin")
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "ffmpeg")
    return dirs


def find_binary(name: str, configured: str = "", extra_dirs: list[Path] | None = None) -> Path | None:
    """Thứ tự: đường dẫn cấu hình → PATH → winget Links → tools\\ffmpeg\\bin."""
    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    found = shutil.which(name)
    if found:
        return Path(found)
    exe = name + ".exe" if sys.platform == "win32" else name
    for d in extra_dirs if extra_dirs is not None else _candidate_dirs():
        p = d / exe
        if p.is_file():
            return p
    return None


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path


def resolve_tools(ffmpeg_path: str = "", ffprobe_path: str = "") -> FFmpegTools:
    ffmpeg = find_binary("ffmpeg", ffmpeg_path)
    ffprobe = find_binary("ffprobe", ffprobe_path)
    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if p is None]
    if missing:
        configured = [p for p in (ffmpeg_path, ffprobe_path) if p]
        detail = f" (đường dẫn trong Cài đặt không tồn tại: {', '.join(configured)})" if configured else ""
        raise DependencyMissing(f"Không tìm thấy {' và '.join(missing)}{detail}.", INSTALL_HINT)
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe)


def tool_version(binary: Path) -> str:
    out = subprocess.run(
        [str(binary), "-hide_banner", "-version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    first = (out.stdout or "").splitlines()
    return first[0] if first else ""


# ---------------------------------------------------------------- probe


@dataclass
class VideoStream:
    index: int
    codec: str
    width: int
    height: int
    fps: float


@dataclass
class AudioStream:
    index: int
    codec: str
    sample_rate: int
    channels: int
    language: str = ""


@dataclass
class MediaInfo:
    path: str
    format_name: str
    duration: float
    size_bytes: int
    bit_rate: int
    video: list[VideoStream] = field(default_factory=list)
    audio: list[AudioStream] = field(default_factory=list)
    subtitle_streams: int = 0

    @property
    def has_video(self) -> bool:
        return bool(self.video)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_fps(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            return round(float(num) / float(den), 3) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _to_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_probe_json(path: str, data: dict) -> MediaInfo:
    fmt = data.get("format") or {}
    info = MediaInfo(
        path=path,
        format_name=fmt.get("format_name", ""),
        duration=_to_float(fmt.get("duration")),
        size_bytes=_to_int(fmt.get("size")),
        bit_rate=_to_int(fmt.get("bit_rate")),
    )
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        disposition = s.get("disposition") or {}
        if kind == "video" and not disposition.get("attached_pic"):
            info.video.append(VideoStream(
                index=_to_int(s.get("index")),
                codec=s.get("codec_name", ""),
                width=_to_int(s.get("width")),
                height=_to_int(s.get("height")),
                fps=_parse_fps(s.get("avg_frame_rate")) or _parse_fps(s.get("r_frame_rate")),
            ))
        elif kind == "audio":
            info.audio.append(AudioStream(
                index=_to_int(s.get("index")),
                codec=s.get("codec_name", ""),
                sample_rate=_to_int(s.get("sample_rate")),
                channels=_to_int(s.get("channels")),
                language=(s.get("tags") or {}).get("language", ""),
            ))
        elif kind == "subtitle":
            info.subtitle_streams += 1
    if info.duration <= 0:
        # Một số container (MKV ghi dở) không có duration ở format; lấy từ stream dài nhất.
        durations = [_to_float(s.get("duration")) for s in data.get("streams") or []]
        info.duration = max(durations, default=0.0)
    return info


def probe(path: Path, tools: FFmpegTools, timeout: float = 60) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise InvalidInput(f"Không tìm thấy file: {path}", "Kiểm tra lại đường dẫn hoặc file đã bị di chuyển/xóa.")
    if not path.is_file():
        raise InvalidInput(f"Đây không phải là file: {path}", "Hãy chọn một file video hoặc âm thanh.")
    cmd = [str(tools.ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(
            f"ffprobe không phản hồi sau {timeout:.0f} giây khi đọc {path.name}.",
            "File có thể nằm trên ổ mạng chậm hoặc bị hỏng. Thử sao chép file về ổ máy.",
        ) from exc
    if out.returncode != 0:
        detail = (out.stderr or "").strip().splitlines()[-1:] or ["không rõ"]
        raise InvalidInput(
            f"Không đọc được file media '{path.name}': {detail[0]}",
            "File có thể bị hỏng hoặc không phải định dạng video/âm thanh được hỗ trợ.",
        )
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ExternalToolError("ffprobe trả về dữ liệu không hợp lệ.", "Cập nhật FFmpeg lên bản mới.") from exc
    info = parse_probe_json(str(path), data)
    if not info.has_video and not info.has_audio:
        raise InvalidInput(
            f"File '{path.name}' không có luồng video hoặc âm thanh.",
            "Hãy chọn file video (MP4, MKV, MOV...) hoặc âm thanh (MP3, WAV...).",
        )
    return info


# ---------------------------------------------------------------- run


def run_ffmpeg(
    tools: FFmpegTools,
    args: list[str],
    ctx: "JobContext | None" = None,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cwd: Path | None = None,
) -> None:
    """Chạy ffmpeg với `args` (không gồm tên chương trình).

    - Tiến trình lấy từ `-progress pipe:1` (thời gian đã xử lý / `duration`).
    - Nếu `ctx` bị hủy: kill tiến trình và ném JobCancelled.
    - Lỗi: ném ExternalToolError kèm các dòng stderr cuối.
    - `cwd`: thư mục làm việc — để bộ lọc tham chiếu file (ass=, -/filter_complex) bằng tên tương đối,
      tránh phải escape đường dẫn Windows (dấu hai chấm của ổ đĩa, dấu tiếng Việt) bên trong cú pháp filter.
    """
    cmd = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1", *args]
    log.info("Chạy: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW, cwd=cwd,
    )
    stderr_tail: deque[str] = deque(maxlen=50)

    def _drain_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip())

    t = threading.Thread(target=_drain_stderr, name="ffmpeg-stderr", daemon=True)
    t.start()
    if ctx is not None:
        ctx.attach_process(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if ctx is not None and ctx.is_cancelled:
                break
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration and duration > 0 and on_progress:
                us = _to_int(value, -1)
                if us >= 0:
                    on_progress(min(1.0, us / 1_000_000 / duration))
        if ctx is not None and ctx.is_cancelled:
            _kill(proc)
            proc.wait()
            raise JobCancelled()
        proc.wait()
    finally:
        if proc.poll() is None:
            _kill(proc)
            proc.wait()
        if ctx is not None:
            ctx.detach_process(proc)
        t.join(timeout=5)

    if ctx is not None and ctx.is_cancelled:
        raise JobCancelled()
    if proc.returncode != 0:
        tail = "\n".join(list(stderr_tail)[-8:])
        log.error("ffmpeg lỗi (mã %s):\n%s", proc.returncode, "\n".join(stderr_tail))
        raise ExternalToolError(
            f"FFmpeg lỗi (mã {proc.returncode}): {tail.splitlines()[-1] if tail else 'không rõ'}",
            "Xem chi tiết trong log của tác vụ. Thường do file nguồn hỏng, codec không hỗ trợ hoặc hết dung lượng.",
        )


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
