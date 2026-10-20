"""Dựng lại video theo giọng đọc mới: cắt cảnh ngắn, đổi thứ tự (hook lên đầu), zoom/crop, chữ, khớp thời lượng giọng.

1. Tìm điểm chuyển cảnh (so sánh khung nhỏ 4 hình/giây) → các "shot" — mảnh cắt không vắt qua chỗ chuyển cảnh.
2. Mỗi đoạn giọng đọc gắn với đoạn kịch bản → dòng transcript nguồn → khoảng thời gian trong video gốc;
   lấy các mảnh 1,2–3 giây rải đều trong khoảng đó (thiếu hình thì nới rộng ra hai bên), tổng đúng bằng thời lượng
   đoạn giọng → hình luôn khớp lời. Hook dùng cảnh của chi tiết hấp dẫn nhất (thường ở cuối video) → đổi thứ tự cảnh.
3. Mỗi mảnh: làm mờ phụ đề Trung cũ (trừ logo/watermark đứng yên — giữ để ghi nguồn) → zoom xen kẽ → đổi khung.
4. Ghép các mảnh + giọng đọc + phụ đề Việt từng câu + tiêu đề lúc mở đầu + dòng ghi nguồn.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.media.ffmpeg import FFmpegTools
from pas.media.frames import iter_frames
from pas.modules.clips.tasks import ASPECTS, video_filter
from pas.modules.vietsub import render as vs_render
from pas.modules.vietsub.ocr import SubEvent

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

FPS = 30
SCENE_FPS = 4
SCENE_SIZE = (64, 36)
MIN_SHOT = 0.5
MIN_PIECE = 1.2
MAX_PIECE = 3.0
EDGE = 0.08  # bỏ vài khung sát điểm chuyển cảnh
ZOOMS = (1.0, 1.12)
ZOOM_LABELS = {"alternate": "Xen kẽ gần/xa (1× / 1,12×)", "none": "Không zoom"}
CAPTION_CHARS = {"9:16": 32, "1:1": 36}
DEFAULT_CAPTION_CHARS = 48


# ---------------------------------------------------------------- chuyển cảnh


def detect_cuts(diffs: list[float], fps: float = SCENE_FPS) -> list[float]:
    """Điểm chuyển cảnh (giây) từ độ chênh trung bình giữa hai khung liên tiếp (diffs[i] = khung i so với i-1)."""
    if not diffs:
        return []
    ordered = sorted(diffs)
    median = ordered[len(ordered) // 2]
    threshold = max(18.0, 4.0 * median)
    return [i / fps for i, d in enumerate(diffs) if i and d > threshold]


def shots_from_cuts(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    shots: list[list[float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a <= 0:
            continue
        if shots and b - a < MIN_SHOT:
            shots[-1][1] = b  # shot quá ngắn (nháy sáng, chuyển cảnh mờ dần) → gộp vào shot trước
        else:
            shots.append([a, b])
    if len(shots) > 1 and shots[0][1] - shots[0][0] < MIN_SHOT:
        shots[1][0] = shots.pop(0)[0]
    return [(round(a, 3), round(b, 3)) for a, b in shots]


def detect_shots(src: Path, tools: FFmpegTools, duration: float, ctx: "JobContext | None" = None,
                 on_progress: Callable[[float], None] | None = None) -> list[tuple[float, float]]:
    import numpy as np

    w, h = SCENE_SIZE
    diffs: list[float] = []
    prev = None
    total = max(1, int(duration * SCENE_FPS))
    for i, frame in enumerate(iter_frames(src, tools, f"fps={SCENE_FPS},scale={w}:{h}", w, h, ctx, "tìm chuyển cảnh")):
        cur = frame.astype(np.int16)
        diffs.append(0.0 if prev is None else float(np.abs(cur - prev).mean()))
        prev = cur
        if on_progress and i % 20 == 0:
            on_progress(min(1.0, i / total))
    return shots_from_cuts(detect_cuts(diffs), duration)


# ---------------------------------------------------------------- lên kế hoạch cảnh


@dataclass(frozen=True)
class Piece:
    index: int
    src_start: float
    duration: float
    zoom: float
    out_start: float  # vị trí trên dòng thời gian video mới

    def to_dict(self) -> dict:
        return {"index": self.index, "src_start": self.src_start, "duration": self.duration, "zoom": self.zoom,
                "out_start": self.out_start}


def source_range(seg: dict, script: dict[int, dict], lines: list[dict], timeline: float, duration: float,
                 seg_start: float) -> tuple[float, float]:
    """Khoảng thời gian trong video gốc ứng với một đoạn giọng đọc."""
    part = script.get(seg.get("script_id")) if seg.get("script_id") is not None else None
    src = part.get("src") if part else None
    if src and lines:
        a, b = max(0, min(src)), min(len(lines) - 1, max(src))
        if a <= b:
            return lines[a]["start"], lines[b]["end"]
    # Không gắn được với kịch bản (người dùng tự viết đoạn mới, lời kêu gọi cuối…): chia theo tỉ lệ thời gian.
    ratio = seg_start / max(timeline, 1e-6)
    seg_len = max(seg["end"] - seg["start"], 1.0) * duration / max(timeline, 1e-6)
    s = ratio * duration
    return s, min(duration, s + seg_len)


def _windows(shots: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    out = []
    for a, b in shots:
        a, b = max(a + EDGE, lo), min(b - EDGE, hi)
        if b - a >= 0.3:
            out.append((a, b))
    return out


def _available(wins: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in wins)


def _overlap_used(a: float, b: float, used: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in used)


def pick_pieces(shots: list[tuple[float, float]], lo: float, hi: float, need: float, duration: float,
                used: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Các mảnh (bắt đầu, độ dài) tổng = `need`, rải đều trong [lo, hi] (nới rộng nếu thiếu), hạn chế dùng lại hình."""
    if need <= 0:
        return []
    n = max(1, math.ceil(need / MAX_PIECE))
    if need / n < MIN_PIECE and n > 1:
        n = max(1, int(need // MIN_PIECE))
    length = need / n
    pad = 0.0
    while True:
        wins = _windows(shots, max(0.0, lo - pad), min(duration, hi + pad))
        fresh = _available(wins) - sum(_overlap_used(a, b, used) for a, b in wins)
        if fresh >= need * 1.15 or (lo - pad <= 0 and hi + pad >= duration):
            break
        pad += max(2.0, (hi - lo) * 0.5)
    if not wins:  # video quá ngắn / toàn chuyển cảnh: lấy thẳng từ đầu khoảng
        wins = [(max(0.0, min(lo, duration - length)), min(duration, max(lo, duration - length) + length))]
    total = _available(wins)
    out: list[tuple[float, float]] = []
    remaining = need
    k = 0
    while remaining > 1e-3:
        piece = min(length, remaining)
        if remaining - piece < MIN_PIECE * 0.5:
            piece = remaining  # đừng để lại mảnh cuối quá vụn
        # vị trí rải đều trên "băng" ghép các cửa sổ, rồi dò tới chỗ chưa dùng gần nhất
        target = (k + 0.5) * total / max(n, k + 1)
        best = None
        for step in range(0, 40):
            pos = (target + step * total / 40) % max(total, 1e-6)
            acc = 0.0
            for a, b in wins:
                if pos < acc + (b - a):
                    start = a + (pos - acc)
                    seg_len = min(piece, b - a)
                    start = min(start, b - seg_len)
                    break
                acc += b - a
            else:
                a, b = wins[-1]
                seg_len = min(piece, b - a)
                start = b - seg_len
            # ưu tiên hình chưa dùng, rồi mảnh đủ dài (cửa sổ ngắn → mảnh hụt → phải thêm mảnh)
            score = _overlap_used(start, start + seg_len, used + out) + 0.5 * (piece - seg_len)
            if best is None or score < best[2] - 1e-6:
                best = (start, seg_len, score)
            if score <= 1e-6:
                break
        start, seg_len, _ = best
        out.append((round(start, 3), round(seg_len, 3)))
        remaining -= seg_len
        k += 1
        if k > n * 4 + 20:  # chặn vòng lặp khi mọi cửa sổ đều quá ngắn
            out[-1] = (out[-1][0], round(out[-1][1] + remaining, 3))
            break
    used.extend((s, s + d) for s, d in out)
    return out


def plan(segments: list[dict], script_parts: list[dict], lines: list[dict], shots: list[tuple[float, float]],
         duration: float, voice_duration: float, zoom: str = "alternate") -> list[Piece]:
    """Kế hoạch dựng: các mảnh liên tiếp phủ kín [0, voice_duration], ranh giới theo đúng khung hình."""
    script = {int(p["id"]): p for p in script_parts}
    used: list[tuple[float, float]] = []
    pieces: list[Piece] = []
    t_out = 0.0
    for i, seg in enumerate(segments):
        seg_end = segments[i + 1]["start"] if i + 1 < len(segments) else voice_duration
        lo, hi = source_range(seg, script, lines, voice_duration, duration, seg["start"])
        for s, d in pick_pieces(shots, lo, hi, seg_end - t_out, duration, used):
            end = round(round((t_out + d) * FPS) / FPS, 6)  # gióng theo khung → không lệch tiếng dần
            if end - t_out < 1 / FPS:
                continue
            z = ZOOMS[len(pieces) % len(ZOOMS)] if zoom == "alternate" else 1.0
            pieces.append(Piece(len(pieces), s, round(end - t_out, 6), z, round(t_out, 6)))
            t_out = end
    if pieces and voice_duration - t_out > 1e-3:  # phần lẻ do làm tròn → kéo dài mảnh cuối
        p = pieces[-1]
        pieces[-1] = Piece(p.index, p.src_start, round(voice_duration - p.out_start, 6), p.zoom, p.out_start)
    return pieces


# ---------------------------------------------------------------- bộ lọc


def piece_filter(piece: Piece, events: list[SubEvent], src_w: int, src_h: int, aspect: str, fit: str) -> str:
    chain = []
    label = "[0:v:0]"
    items = vs_render.local_events([e for e in events if not e.static], piece.src_start, piece.duration)
    groups = vs_render.blur_groups(items, src_w, src_h) if items else []
    if groups:
        chain.append(vs_render.blur_graph(groups, label, "[vb]"))
        label = "[vb]"
    if piece.zoom > 1.0:
        chain.append(f"{label}crop=trunc(iw/{piece.zoom}/2)*2:trunc(ih/{piece.zoom}/2)*2,setsar=1[vz]")
        label = "[vz]"
    chain.append(video_filter(aspect, fit, label, "[vf]"))
    # tpad: nguồn hết hình sớm thì giữ khung cuối → mảnh luôn đủ số khung đã định (không lệch tiếng)
    chain.append(f"[vf]fps={FPS},tpad=stop_mode=clone:stop_duration=5,format=yuv420p[v]")
    return ";".join(chain)


def piece_args(src: Path, piece: Piece, filter_file: str, out: Path, with_audio: bool) -> list[str]:
    audio = (["-map", "0:a:0", "-af", f"aresample=48000,apad,atrim=0:{piece.duration:.6f}", "-ac", "2",
              "-c:a", "pcm_s16le"] if with_audio else ["-an"])
    return ["-ss", f"{piece.src_start:.3f}", "-i", str(src), "-/filter_complex", filter_file, "-map", "[v]",
            "-frames:v", str(max(1, round(piece.duration * FPS))), *audio,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-sn", "-dn", "-map_metadata", "-1", "-f", "matroska", "-y", str(out)]


def output_size(aspect: str, src_w: int, src_h: int) -> tuple[int, int]:
    size = ASPECTS[aspect]
    return size if size else (src_w & ~1, src_h & ~1)


# ---------------------------------------------------------------- chữ (ASS)


def split_caption(text: str, max_chars: int) -> list[str]:
    """Chia câu dài thành các dòng phụ đề ≤ max_chars, ưu tiên ngắt sau dấu phẩy."""
    words = text.split()
    out: list[str] = []
    cur: list[str] = []
    for w in words:
        if cur and len(" ".join(cur + [w])) > max_chars:
            # lùi về dấu phẩy gần nhất nếu nó không quá sớm
            cut = next((i for i in range(len(cur) - 1, 0, -1) if cur[i - 1].endswith((",", ";", ":"))
                        and len(" ".join(cur[:i])) >= max_chars * 0.5), len(cur))
            out.append(" ".join(cur[:cut]))
            cur = cur[cut:]
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def caption_events(segments: list[dict], aspect: str) -> list[tuple[float, float, str]]:
    limit = CAPTION_CHARS.get(aspect, DEFAULT_CAPTION_CHARS)
    out = []
    for seg in segments:
        for s in seg.get("sentences", []):
            parts = split_caption(s["text"], limit)
            total = sum(len(p) for p in parts) or 1
            t = s["start"]
            span = s["end"] - s["start"]
            for p in parts:
                d = span * len(p) / total
                out.append((round(t, 3), round(t + d, 3), p))
                t += d
    return out


def build_ass(captions: list[tuple[float, float, str]], size: tuple[int, int], aspect: str, title: str = "",
              title_end: float = 0.0, credit: str = "") -> str:
    w, h = size
    short = min(w, h)
    cap = int(short * 0.06)
    margin_v = int(h * (0.18 if aspect == "9:16" else 0.08))
    side = int(w * 0.06)
    font = vs_render.FONT
    styles = [
        # Phụ đề lời đọc: chữ trắng viền đen dày, dưới khung (chừa chỗ giao diện TikTok/Shorts ở 9:16)
        f"Style: Cap,{font},{cap},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, cap // 14)},1,2,{side},{side},{margin_v},1",
        # Tiêu đề mở đầu: chữ vàng trên nền đen mờ, phía trên khung
        f"Style: Title,{font},{int(short * 0.075)},&H0000E5FF,&H0000E5FF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,3,"
        f"{max(4, short // 90)},0,8,{side},{side},{int(h * (0.12 if aspect == '9:16' else 0.06))},1",
        # Ghi nguồn: chữ nhỏ mờ góc trên trái
        f"Style: Credit,{font},{max(16, int(short * 0.028))},&H40FFFFFF,&H40FFFFFF,&H80000000,&H00000000,0,0,0,0,"
        f"100,100,0,0,1,1,0,7,{int(w * 0.03)},{int(w * 0.03)},{int(h * 0.025)},1",
    ]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = vs_render._ass_time
    end_all = max((e for _, e, _ in captions), default=title_end)
    if credit:
        lines.append(f"Dialogue: 2,{t(0)},{t(end_all + 1)},Credit,,0,0,0,,{vs_render._ass_text(credit)}")
    if title and title_end > 0:
        lines.append(f"Dialogue: 1,{t(0)},{t(title_end)},Title,,0,0,0,,{vs_render._ass_text(title)}")
    for s, e, text in captions:
        lines.append(f"Dialogue: 0,{t(s)},{t(e)},Cap,,0,0,0,,{vs_render._ass_text(text)}")
    return "\n".join(lines) + "\n"


def final_args(concat_file: str, voice: Path, ass_file: str, out: Path, duration: float, bg_volume: float) -> list[str]:
    if bg_volume > 0:
        graph = (f"[0:v]ass=filename={ass_file}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo[vo];"
                 f"[0:a]volume={bg_volume:.2f}[bg];[vo][bg]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        graph = f"[0:v]ass=filename={ass_file}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo[a]"
    return ["-f", "concat", "-safe", "0", "-i", concat_file, "-i", str(voice), "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-map_metadata", "-1",
            "-f", "mp4", "-y", str(out)]
