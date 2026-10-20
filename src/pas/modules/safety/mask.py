"""Mặt nạ làm mờ: video xám 4 khung/giây (trắng = che), mã hóa FFV1 không mất dữ liệu, mọi khung là keyframe
để `-ss` cắt đúng chỗ. Lúc mã hóa clip, mặt nạ được phóng lên khung gốc và trộn bản mờ của khung vào vùng trắng.
"""

from __future__ import annotations

import logging
import math
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from pas.core.errors import ExternalToolError, JobCancelled
from pas.media.ffmpeg import CREATE_NO_WINDOW, FFmpegTools
from pas.modules.safety.scan import SAMPLE_FPS, Detection, sample_size

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

MASK_FILE = "safety_mask.mkv"
BLUR_DOWNSCALE = 24  # mờ mạnh: thu nhỏ 24 lần rồi phóng lại — không đọc được chữ, không nhận ra chi tiết


def frame_rects(dets: list[Detection], duration: float, src_w: int, src_h: int,
                fps: float = SAMPLE_FPS) -> tuple[int, int, int, dict[int, list[tuple[int, int, int, int]]]]:
    """(rộng, cao, số khung, {khung: [x0, y0, x1, y1]}) theo pixel mặt nạ. Khung k phủ [k/fps, (k+1)/fps)."""
    w, h = sample_size(src_w, src_h)
    sx, sy = w / src_w, h / src_h
    n = math.ceil(duration * fps) + 1
    rects: dict[int, list[tuple[int, int, int, int]]] = {}
    for d in dets:
        if d.box is None:
            r = (0, 0, w, h)
        else:
            x0, y0, x1, y1 = d.box
            r = (max(0, math.floor(x0 * sx)), max(0, math.floor(y0 * sy)),
                 min(w, math.ceil(x1 * sx)), min(h, math.ceil(y1 * sy)))
            if r[2] <= r[0] or r[3] <= r[1]:
                continue
        k0 = max(0, math.floor((d.t - d.hold) * fps))
        k1 = min(n - 1, math.floor((d.t + d.hold) * fps))
        for k in range(k0, k1 + 1):
            rects.setdefault(k, []).append(r)
    return w, h, n, rects


def write_mask(path: Path, tools: FFmpegTools, dets: list[Detection], duration: float, src_w: int, src_h: int,
               ctx: "JobContext | None" = None) -> Path:
    import numpy as np

    w, h, n, rects = frame_rects(dets, duration, src_w, src_h)
    cmd = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{SAMPLE_FPS:g}", "-i", "pipe:0",
           "-c:v", "ffv1", "-g", "1", "-y", str(path)]  # -y: file nằm trong thư mục tạm của chính tác vụ
    log.info("Ghi mặt nạ (%d khung, %d khung có che): %s", n, len(rects), subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    tail: deque[str] = deque(maxlen=20)

    def _drain():
        assert proc.stderr is not None
        for raw in proc.stderr:
            tail.append(raw.decode("utf-8", "replace").rstrip())

    th = threading.Thread(target=_drain, name="mask-ffmpeg-stderr", daemon=True)
    th.start()
    if ctx is not None:
        ctx.attach_process(proc)
    blank = bytes(w * h)
    frame = np.zeros((h, w), dtype=np.uint8)
    broken = False
    try:
        assert proc.stdin is not None
        for k in range(n):
            if ctx is not None and k % 64 == 0:
                ctx.checkpoint()
            if k in rects:
                frame[:] = 0
                for x0, y0, x1, y1 in rects[k]:
                    frame[y0:y1, x0:x1] = 255
                data = frame.tobytes()
            else:
                data = blank
            try:
                proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                broken = True
                break
        try:
            proc.stdin.close()
        except OSError:
            pass
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
        th.join(timeout=5)
    if ctx is not None and ctx.is_cancelled:
        raise JobCancelled()
    if proc.returncode != 0 or broken:
        raise ExternalToolError(f"FFmpeg lỗi khi ghi mặt nạ làm mờ: {(list(tail)[-1:] or ['không rõ'])[0]}",
                                "Xem log tác vụ. Kiểm tra FFmpeg có hỗ trợ bộ mã FFV1 (bản Gyan có sẵn).")
    return path


def safety_graph(src_w: int, src_h: int, src_label: str, mask_label: str, out_label: str) -> str:
    """Trộn bản mờ mạnh của khung vào vùng trắng của mặt nạ. Kích thước theo khung gốc (đã làm chẵn)."""
    w, h = src_w & ~1, src_h & ~1
    bw, bh = max(2, w // BLUR_DOWNSCALE), max(2, h // BLUR_DOWNSCALE)
    r = max(1, min(2, min(bw, bh) // 2 - 1))
    return (f"{src_label}scale={w}:{h},format=yuv420p,split=2[sa][sb];"
            f"[sb]scale={bw}:{bh}:flags=area,boxblur={r}:1,scale={w}:{h}:flags=bilinear[sbl];"
            f"{mask_label}scale={w}:{h}:flags=bilinear,format=gray[sm];"
            f"[sbl][sm]alphamerge[sbm];"
            f"[sa][sbm]overlay=0:0:format=yuv420{out_label}")
