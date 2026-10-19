"""Tác vụ "Tạo clip ngắn": thả một video vào → nhiều clip MP4 (H.264/AAC) cùng độ dài, khung 9:16 / 1:1 / 16:9.

Các bước: probe → lập kế hoạch cắt → kiểm tra dung lượng → mã hóa từng clip → kiểm tra file đầu ra.
- Mỗi clip ghi ra `*.mp4.part` rồi mới đổi tên: không bao giờ để lại file MP4 dở dang.
- Tiến độ lưu ở thư mục tạm của tác vụ (được giữ khi lỗi/gián đoạn): "Chạy lại" bỏ qua clip đã xong.
- Kết quả nằm trong một thư mục mới; file gốc chỉ được đọc.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pas.core.config import Settings
from pas.core.disk import GB, ensure_free_space
from pas.core.errors import ExternalToolError, InvalidInput
from pas.core.paths import ensure_not_source, unique_output_dir
from pas.jobs.context import JobContext
from pas.jobs.registry import TaskRegistry, TaskSpec
from pas.media.ffmpeg import FFmpegTools, MediaInfo, probe, run_ffmpeg

log = logging.getLogger(__name__)

KIND = "video.clips"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}

# Kích thước đầu ra theo khung. "original" = giữ khung gốc.
ASPECTS: dict[str, tuple[int, int] | None] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "original": None,
}
ASPECT_LABELS = {
    "9:16": "9:16 dọc (TikTok, Shorts, Reels)",
    "1:1": "1:1 vuông",
    "16:9": "16:9 ngang (YouTube)",
    "original": "Giữ khung gốc",
}
FITS = {
    "blur": "Nền mờ (giữ toàn bộ hình)",
    "crop": "Cắt phần giữa (lấp đầy khung)",
    "pad": "Viền đen (giữ toàn bộ hình)",
}

MIN_SEGMENT = 5
MAX_SEGMENT = 3600
# Đoạn cuối ngắn hơn tỉ lệ này của độ dài clip sẽ được gộp vào clip trước (tránh clip 2–3 giây vô nghĩa).
TAIL_MERGE_RATIO = 0.3
# Sai lệch thời lượng cho phép khi kiểm tra file đầu ra.
DURATION_TOLERANCE = 0.5


@dataclass(frozen=True)
class Segment:
    index: int  # bắt đầu từ 1
    start: float
    duration: float


def plan_segments(total: float, segment_seconds: float) -> list[Segment]:
    if total <= 0:
        return []
    starts: list[float] = []
    t = 0.0
    while t < total - 1e-6:
        starts.append(t)
        t += segment_seconds
    if len(starts) > 1 and total - starts[-1] < segment_seconds * TAIL_MERGE_RATIO:
        starts.pop()
    segments = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        segments.append(Segment(i + 1, round(start, 3), round(end - start, 3)))
    return segments


def video_filter(aspect: str, fit: str) -> str:
    """Chuỗi -filter_complex: vào [0:v:0], ra [v]."""
    size = ASPECTS[aspect]
    if size is None:
        return "[0:v:0]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[v]"
    w, h = size
    if fit == "crop":
        return f"[0:v:0]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[v]"
    if fit == "pad":
        return (f"[0:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v]")
    # Nền mờ: làm mờ trên ảnh nhỏ rồi phóng to — nhẹ hơn nhiều so với mờ ở 1080p trên CPU.
    sw, sh = w // 8, h // 8
    return (f"[0:v:0]split=2[a][b];"
            f"[a]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},boxblur=8:2,scale={w}:{h}[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")


def encode_args(src: Path, seg: Segment, aspect: str, fit: str, out: Path) -> list[str]:
    return [
        "-ss", f"{seg.start:.3f}", "-i", str(src), "-t", f"{seg.duration:.3f}",
        "-filter_complex", video_filter(aspect, fit),
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-movflags", "+faststart", "-map_metadata", "-1", "-sn", "-dn",
        "-f", "mp4", "-y", str(out),  # -y chỉ áp dụng cho file .part của chính tác vụ này
    ]


def estimate_bytes(info: MediaInfo, aspect: str) -> int:
    size = ASPECTS[aspect]
    v = info.video[0]
    w, h = size if size else (v.width, v.height)
    fps = min(v.fps or 30.0, 60.0)
    video_bps = w * h * fps * 0.1  # ước lượng rộng tay cho x264 CRF 22
    return int(info.duration * (video_bps + 160_000) / 8)


def verify_output(path: Path, tools: FFmpegTools, seg: Segment, aspect: str, expect_audio: bool) -> MediaInfo:
    """Module F: file đầu ra phải đọc được, đúng codec, đúng khung, đúng thời lượng."""
    try:
        info = probe(path, tools)
    except InvalidInput as exc:
        raise ExternalToolError(f"Clip {seg.index} xuất ra không đọc được: {exc.message}",
                                "Có thể ổ đĩa đầy hoặc FFmpeg bị dừng giữa chừng. Bấm Chạy lại.") from exc
    problems = []
    if not info.video or info.video[0].codec != "h264":
        problems.append("không có luồng video H.264")
    elif ASPECTS[aspect] is not None and (info.video[0].width, info.video[0].height) != ASPECTS[aspect]:
        problems.append(f"kích thước {info.video[0].width}×{info.video[0].height}, cần {ASPECTS[aspect][0]}×{ASPECTS[aspect][1]}")
    if expect_audio and not any(a.codec == "aac" for a in info.audio):
        problems.append("thiếu âm thanh AAC")
    if abs(info.duration - seg.duration) > DURATION_TOLERANCE:
        problems.append(f"thời lượng {info.duration:.2f}s, cần {seg.duration:.2f}s")
    if problems:
        raise ExternalToolError(f"Clip {seg.index} không đạt kiểm tra: {'; '.join(problems)}.",
                                "Xem log tác vụ. Nếu lặp lại, thử khung 'Giữ khung gốc' hoặc kiểm tra file gốc.")
    return info


def validate(params: dict) -> None:
    raw = params.get("path")
    if not raw:
        raise InvalidInput("Chưa chọn video.", "Bấm 'Chọn…' hoặc kéo thả video vào ô.")
    path = Path(raw)
    if not path.is_file():
        raise InvalidInput(f"Không tìm thấy file: {path}", "Kiểm tra lại đường dẫn.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise InvalidInput(
            f"Định dạng '{path.suffix or '(không có đuôi)'}' không phải video được hỗ trợ.",
            "Hỗ trợ: " + ", ".join(sorted(e.lstrip('.').upper() for e in VIDEO_EXTENSIONS)),
        )
    seconds = params.get("segment_seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not MIN_SEGMENT <= seconds <= MAX_SEGMENT:
        raise InvalidInput(f"Độ dài mỗi clip phải từ {MIN_SEGMENT} đến {MAX_SEGMENT} giây.")
    if params.get("aspect") not in ASPECTS:
        raise InvalidInput(f"Khung hình không hợp lệ: {params.get('aspect')!r}")
    if params.get("fit") not in FITS:
        raise InvalidInput(f"Cách lấp khung không hợp lệ: {params.get('fit')!r}")
    out = params.get("output_dir")
    if out and Path(out).exists() and not Path(out).is_dir():
        raise InvalidInput(f"Thư mục đầu ra không phải thư mục: {out}")


def folder_name(src: Path, aspect: str) -> str:
    tag = "goc" if aspect == "original" else aspect.replace(":", "x")
    return f"{src.stem} - clip {tag}"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def make_handler(tools_provider: Callable[[], FFmpegTools], settings_provider: Callable[[], Settings]):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        src = Path(p["path"])
        aspect, fit, seconds = p["aspect"], p["fit"], float(p["segment_seconds"])

        ctx.report(0.01, "Đang đọc thông tin video")
        tools = tools_provider()
        info = probe(src, tools)
        if not info.has_video:
            raise InvalidInput(f"'{src.name}' không có hình ảnh.", "Chọn một file video.")
        if info.duration <= 0:
            raise InvalidInput(f"Không xác định được thời lượng của '{src.name}'.",
                               "File có thể ghi dở hoặc hỏng. Thử mở bằng trình phát video.")
        segments = plan_segments(info.duration, seconds)
        ctx.log(f"Video {info.duration:.2f}s → {len(segments)} clip ~{seconds:g}s, khung {aspect}, {fit}")
        ctx.checkpoint()

        state_file = ctx.temp_dir / "clips_state.json"
        state = _load_state(state_file)
        out_dir = Path(state["out_dir"]) if state.get("out_dir") else None
        if out_dir is None or not out_dir.is_dir():
            base = Path(p.get("output_dir") or settings_provider().default_output_dir or src.parent)
            out_dir = unique_output_dir(base / folder_name(src, aspect))
            state = {"out_dir": str(out_dir), "done": []}
        else:
            ctx.log(f"Chạy tiếp vào {out_dir} (đã xong {len(state.get('done', []))} clip)")

        remaining = [s for s in segments if s.index not in state.get("done", [])]
        ratio = sum(s.duration for s in remaining) / info.duration if info.duration else 1.0
        min_free = int(settings_provider().min_free_disk_gb * GB)
        ensure_free_space(out_dir, int(estimate_bytes(info, aspect) * ratio) + min_free)
        out_dir.mkdir(parents=True, exist_ok=True)
        _save_state(state_file, state)

        width = len(str(len(segments)))
        files: list[dict] = []
        for seg in segments:
            final = out_dir / f"{src.stem} - phần {seg.index:0{width}d}.mp4"
            ensure_not_source(final, src)
            start_p = 0.03 + 0.95 * (seg.start / info.duration)
            end_p = 0.03 + 0.95 * ((seg.start + seg.duration) / info.duration)
            if seg.index in state["done"] and final.is_file():
                out_info = verify_output(final, tools, seg, aspect, info.has_audio)
                ctx.log(f"Bỏ qua clip {seg.index} (đã có từ lần chạy trước)")
            else:
                ctx.checkpoint()
                ctx.report(start_p, f"Đang tạo clip {seg.index}/{len(segments)}")
                if final.exists():  # file trùng tên không do tác vụ này tạo: không ghi đè
                    raise InvalidInput(f"Đã có file {final}.", "Chuyển file đó đi hoặc chọn thư mục đầu ra khác.")
                part = final.with_name(final.name + ".part")
                try:
                    run_ffmpeg(tools, encode_args(src, seg, aspect, fit, part), ctx, seg.duration,
                               ctx.sub_progress(start_p, end_p))
                    out_info = verify_output(part, tools, seg, aspect, info.has_audio)
                    os.replace(part, final)
                finally:
                    part.unlink(missing_ok=True)
                state["done"].append(seg.index)
                _save_state(state_file, state)
                ctx.log(f"Xong clip {seg.index}: {final.name} ({out_info.duration:.2f}s)")
            files.append({"path": str(final), "index": seg.index, "start": seg.start,
                          "duration": round(out_info.duration, 3), "size_bytes": final.stat().st_size})

        ctx.report(1.0, f"Đã tạo {len(files)} clip")
        return {"source": str(src), "output_dir": str(out_dir), "aspect": aspect, "fit": fit,
                "segment_seconds": seconds, "source_duration": info.duration, "files": files}

    return run


def register(registry: TaskRegistry, tools_provider: Callable[[], FFmpegTools],
             settings_provider: Callable[[], Settings]) -> None:
    registry.register(TaskSpec(KIND, "Tạo clip ngắn", make_handler(tools_provider, settings_provider), validate))
