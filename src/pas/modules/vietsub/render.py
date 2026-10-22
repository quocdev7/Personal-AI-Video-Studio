"""Dựng bộ lọc FFmpeg: làm mờ đúng vùng chữ Trung (chỉ trong lúc chữ hiện) + phụ đề tiếng Việt (ASS) đặt đè lên.

Làm mờ thực hiện trên khung gốc (tọa độ OCR) trước khi đổi khung; phụ đề ASS vẽ sau cùng theo tọa độ khung đầu ra.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pas.modules.vietsub.ocr import Box, SubEvent, _iou, _union, _v_overlap

MAX_BLUR_GROUPS = 24
# OCR lấy mẫu SAMPLE_FPS khung/s nên mốc đầu/cuối của câu lệch tới 1/SAMPLE_FPS giây: làm mờ nới rộng hai đầu
# (phụ đề ASS vẫn theo mốc gốc) và nối khoảng trống ngắn giữa hai câu cùng chỗ (câu quá ngắn OCR có thể sót).
BLUR_PAD = 0.3
BLUR_BRIDGE = 0.8
FONT = "Arial"


@dataclass(frozen=True)
class Fit:
    """Ánh xạ tọa độ gốc → khung đầu ra: x' = x·scale + ox."""
    scale: float
    ox: float
    oy: float
    out_w: int
    out_h: int


def fit_transform(src_w: int, src_h: int, size: tuple[int, int] | None, fit: str) -> Fit:
    if size is None:
        return Fit(1.0, 0.0, 0.0, src_w & ~1, src_h & ~1)
    w, h = size
    scale = max(w / src_w, h / src_h) if fit == "crop" else min(w / src_w, h / src_h)
    return Fit(scale, (w - src_w * scale) / 2, (h - src_h * scale) / 2, w, h)


@dataclass
class BlurGroup:
    box: tuple[int, int, int, int]  # x, y, w, h (chẵn, nằm trong khung gốc)
    windows: list[tuple[float, float]]  # (bắt đầu, kết thúc) theo thời gian của clip


def local_events(events: list[SubEvent], start: float, duration: float) -> list[tuple[SubEvent, float, float]]:
    """Sự kiện giao với đoạn [start, start+duration), thời gian đổi về gốc 0 của clip."""
    end = start + duration
    out = []
    for e in events:
        if e.end <= start or e.start >= end:
            continue
        out.append((e, round(max(0.0, e.start - start), 3), round(min(duration, e.end - start), 3)))
    return out


def blur_items(events: list[SubEvent], start: float, duration: float,
               pad: float = BLUR_PAD) -> list[tuple[SubEvent, float, float]]:
    """Như local_events nhưng mỗi sự kiện được nới ±pad giây trước khi cắt theo đoạn — chỉ dùng để làm mờ."""
    end = start + duration
    out = []
    for e in events:
        s, t = e.start - pad, e.end + pad
        if t <= start or s >= end:
            continue
        out.append((e, round(max(0.0, s - start), 3), round(min(duration, t - start), 3)))
    return out


def _padded(e: SubEvent, src_w: int, src_h: int) -> Box:
    lh = max(e.line_height, 1.0)
    x0, y0, x1, y1 = e.box
    return max(0.0, x0 - 0.5 * lh), max(0.0, y0 - 0.3 * lh), min(float(src_w), x1 + 0.5 * lh), min(float(src_h), y1 + 0.3 * lh)


def blur_groups(items: list[tuple[SubEvent, float, float]], src_w: int, src_h: int) -> list[BlurGroup]:
    """Gom các vùng chữ trùng chỗ thành nhóm (mỗi nhóm = 1 crop+blur+overlay) để bộ lọc không phình theo số câu."""
    groups: list[tuple[Box, list[tuple[float, float]]]] = []
    for e, s, t in items:
        box = _padded(e, src_w, src_h)
        for gi, (gbox, wins) in enumerate(groups):
            same_row = _v_overlap(gbox, box) > 0.6 and min(gbox[2], box[2]) > max(gbox[0], box[0])
            if same_row or _iou(gbox, box) > 0.3:
                groups[gi] = (_union(gbox, box), wins + [(s, t)])
                break
        else:
            groups.append((box, [(s, t)]))
    while len(groups) > MAX_BLUR_GROUPS:
        groups.sort(key=lambda g: g[0][1])
        best = min(range(len(groups) - 1), key=lambda i: _union(groups[i][0], groups[i + 1][0])[3] - groups[i][0][1])
        a, b = groups[best], groups.pop(best + 1)
        groups[best] = (_union(a[0], b[0]), a[1] + b[1])
    out = []
    for box, wins in groups:
        x0, y0 = int(box[0]) & ~1, int(box[1]) & ~1
        x1, y1 = min(src_w & ~1, (int(box[2] + 1.999) + 1) & ~1), min(src_h & ~1, (int(box[3] + 1.999) + 1) & ~1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        out.append(BlurGroup((x0, y0, x1 - x0, y1 - y0), _merge_windows(wins)))
    return out


def _merge_windows(wins: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for s, t in sorted(wins):
        if merged and s <= merged[-1][1] + BLUR_BRIDGE:
            merged[-1][1] = max(merged[-1][1], t)
        else:
            merged.append([s, t])
    return [(a, b) for a, b in merged]


def blur_graph(groups: list[BlurGroup], src_label: str, out_label: str) -> str:
    """Chuỗi filter_complex làm mờ từng nhóm trong khoảng thời gian chữ hiện."""
    n = len(groups)
    parts = [f"{src_label}split={n + 1}[vs0]" + "".join(f"[vs{i + 1}]" for i in range(n))]
    for i, g in enumerate(groups, 1):
        x, y, w, h = g.box
        lr = max(1, min(24, min(w, h) // 2 - 1))
        cr = max(1, min(12, min(w, h) // 4 - 1))
        parts.append(f"[vs{i}]crop={w}:{h}:{x}:{y},boxblur=luma_radius={lr}:luma_power=3:"
                     f"chroma_radius={cr}:chroma_power=3[vb{i}]")
    prev = "[vs0]"
    for i, g in enumerate(groups, 1):
        x, y, _, _ = g.box
        enable = "+".join(f"between(t,{s:.3f},{t:.3f})" for s, t in g.windows)
        nxt = out_label if i == n else f"[vo{i}]"
        parts.append(f"{prev}[vb{i}]overlay={x}:{y}:enable='{enable}'{nxt}")
        prev = nxt
    return ";".join(parts)


# ---------------------------------------------------------------- phụ đề


def font_size(items: list[tuple[SubEvent, float, float]], fit: Fit) -> int:
    heights = sorted(e.line_height for e, _, _ in items) or [0.0]
    mapped = heights[len(heights) // 2] * fit.scale
    short = min(fit.out_w, fit.out_h)
    return int(min(max(mapped, short * 0.045), fit.out_h * 0.08))


def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    # Ký tự điều khiển của ASS trong bản dịch không được hiểu thành lệnh định dạng.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(items: list[tuple[SubEvent, float, float]], fit: Fit) -> str:
    """Phụ đề Việt đặt tại tâm vùng chữ Trung (sau khi đổi khung), chữ trắng viền đen."""
    subs = [(e, s, t) for e, s, t in items if e.vi and not e.static]
    size = font_size(subs, fit)
    margin = int(fit.out_w * 0.05)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {fit.out_w}", f"PlayResY: {fit.out_h}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, size // 14)},1,5,{margin},{margin},{margin},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for e, s, t in subs:
        cx = (e.box[0] + e.box[2]) / 2 * fit.scale + fit.ox
        cy = (e.box[1] + e.box[3]) / 2 * fit.scale + fit.oy
        cx = min(max(cx, fit.out_w * 0.3), fit.out_w * 0.7)
        cy = min(max(cy, size * 1.2), fit.out_h - size * 1.2)
        lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(t)},Default,,0,0,0,,"
                     f"{{\\an5\\pos({cx:.0f},{cy:.0f})}}{_ass_text(e.vi)}")
    return "\n".join(lines) + "\n"


def _srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(events: list[SubEvent], lang: str) -> str:
    """lang 'vi' hoặc 'zh'. Bỏ logo/watermark; bản Việt bỏ câu chưa dịch được."""
    rows = []
    for e in events:
        text = e.vi if lang == "vi" else e.text
        if e.static or not text:
            continue
        rows.append(f"{len(rows) + 1}\n{_srt_time(e.start)} --> {_srt_time(e.end)}\n{text}\n")
    return "\n".join(rows)


def write_text_atomic(path: Path, text: str) -> None:
    import os

    part = path.with_name(path.name + ".part")
    # BOM cho .srt/.txt: Notepad và trình phát cũ trên Windows mới nhận đúng tiếng Việt.
    part.write_text(text, encoding="utf-8-sig" if path.suffix.lower() in (".srt", ".txt") else "utf-8")
    os.replace(part, path)
