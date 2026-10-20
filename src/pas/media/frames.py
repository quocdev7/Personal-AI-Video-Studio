"""Đọc khung hình thô (bgr24) từ FFmpeg qua pipe để phân tích bằng numpy — dùng chung cho OCR và kiểm duyệt."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from pas.core.errors import ExternalToolError, JobCancelled
from pas.media.ffmpeg import CREATE_NO_WINDOW, FFmpegTools

if TYPE_CHECKING:
    import numpy as np

    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)


def iter_frames(src: Path, tools: FFmpegTools, vf: str, width: int, height: int,
                ctx: "JobContext | None" = None, purpose: str = "phân tích") -> Iterator["np.ndarray"]:
    """Sinh từng khung `height × width × 3` (BGR, uint8) sau bộ lọc `vf` (phải kết thúc ở đúng kích thước đó).

    Hủy tác vụ → FFmpeg bị dừng ngay và ném JobCancelled; FFmpeg lỗi → ExternalToolError. Dừng vòng lặp sớm
    (break) cũng dọn tiến trình.
    """
    import numpy as np

    cmd = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src),
           "-map", "0:v:0", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    log.info("Đọc khung (%s): %s", purpose, subprocess.list2cmdline(cmd))
    frame_bytes = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW, bufsize=frame_bytes * 2)
    stderr_tail: deque[str] = deque(maxlen=20)

    def _drain():
        assert proc.stderr is not None
        for raw in proc.stderr:
            stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    t = threading.Thread(target=_drain, name="frames-ffmpeg-stderr", daemon=True)
    t.start()
    if ctx is not None:
        ctx.attach_process(proc)
    try:
        assert proc.stdout is not None
        while True:
            if ctx is not None:
                ctx.checkpoint()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
        proc.wait()
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()
        if ctx is not None:
            ctx.detach_process(proc)
        t.join(timeout=5)
    if ctx is not None and ctx.is_cancelled:
        raise JobCancelled()
    if proc.returncode != 0:
        tail = list(stderr_tail)[-1:] or ["không rõ"]
        raise ExternalToolError(f"FFmpeg lỗi khi đọc khung hình để {purpose}: {tail[0]}",
                                "File video có thể hỏng. Thử mở bằng trình phát video.")
