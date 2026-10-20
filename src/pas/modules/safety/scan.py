"""Quét video một lượt: mỗi khung mẫu (4 khung/giây, rộng ≤ 640) đưa qua các bộ nhận diện được bật.

- Khỏa thân: mọi khung mẫu, có vị trí → che đúng vùng.
- Bạo lực / máu me: 1 khung/giây; chỉ tính khi ≥ 2 khung liên tiếp vượt ngưỡng (giảm báo nhầm) → che các vùng
  điểm cao nhất của khung (cửa sổ CLIP, ≤ 35% khung), không che cả khung.
- Chữ thô tục: OCR 1 khung/giây (lệch pha với CLIP), dòng chữ có từ trong danh sách → che dòng đó.
Tọa độ trả về theo pixel video gốc.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.media.ffmpeg import FFmpegTools
from pas.media.frames import iter_frames
from pas.modules.safety.detect import EXPOSED

if TYPE_CHECKING:
    from pas.jobs.context import JobContext
    from pas.modules.safety.detect import NudityDetector, ViolenceClassifier
    from pas.modules.safety.words import WordFilter

log = logging.getLogger(__name__)

SAMPLE_FPS = 4.0
MAX_WIDTH = 640
CLIP_EVERY = 4  # 1 khung/giây
OCR_EVERY = 4
OCR_OFFSET = 2
NUDITY_THRESHOLD = 0.35
VIOLENCE_THRESHOLD = 0.6
VIOLENCE_MIN_RUN = 2
NUDITY_HOLD = 0.5  # giây che thêm trước/sau mỗi lần thấy (khung mẫu cách nhau 0,25 s)
# < nửa khoảng cách mẫu (1 s): mỗi khung mặt nạ chỉ nhận vùng của mẫu gần nhất. Nếu giữ lâu hơn, vùng của các giây
# kề nhau chồng lên nhau và tổng diện tích che vượt xa giới hạn ~1/3 khung của mỗi mẫu.
VIOLENCE_HOLD = 0.45
TEXT_HOLD = 1.0
BOX_MARGIN = 0.15

KINDS = {"nudity": "Khỏa thân", "violence": "Bạo lực / máu me", "text": "Chữ thô tục"}

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    t: float  # giây, theo video gốc
    kind: str  # nudity | violence | text
    label: str
    score: float
    box: Box | None  # None = cả khung (chỉ còn trong kết quả quét đã lưu của bản cũ)
    hold: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["box"] = list(self.box) if self.box else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Detection":
        return cls(float(d["t"]), d["kind"], d["label"], float(d["score"]),
                   tuple(d["box"]) if d.get("box") else None, float(d["hold"]))


def sample_size(src_w: int, src_h: int) -> tuple[int, int]:
    w = max(2, min(MAX_WIDTH, src_w) & ~1)
    h = max(2, round(src_h * w / src_w) & ~1)
    return w, h


def _grow(box: Box, margin: float, w: float, h: float) -> Box:
    x0, y0, x1, y1 = box
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return max(0.0, x0 - mx), max(0.0, y0 - my), min(w, x1 + mx), min(h, y1 + my)


ViolenceSample = tuple[float, float, str, list[Box]]


def violence_runs(samples: list[ViolenceSample], threshold: float = VIOLENCE_THRESHOLD,
                  min_run: int = VIOLENCE_MIN_RUN) -> list[Detection]:
    """samples: (t, điểm, nhãn, vùng cần che) theo thứ tự thời gian, 1 mẫu/giây. Chỉ giữ chuỗi ≥ min_run mẫu liên
    tiếp vượt ngưỡng; mỗi vùng thành một Detection."""
    out: list[Detection] = []
    run: list[ViolenceSample] = []
    for s in samples + [(math.inf, 0.0, "", [])]:
        if s[1] >= threshold:
            run.append(s)
            continue
        if len(run) >= min_run:
            out += [Detection(round(t, 3), "violence", lab, sc, box, VIOLENCE_HOLD)
                    for t, sc, lab, boxes in run for box in boxes]
        run = []
    return out


def scan_video(
    src: Path,
    tools: FFmpegTools,
    src_w: int,
    src_h: int,
    duration: float,
    ctx: "JobContext | None" = None,
    on_progress: Callable[[float], None] | None = None,
    nudity: "NudityDetector | None" = None,
    violence: "ViolenceClassifier | None" = None,
    ocr_engine=None,
    words: "WordFilter | None" = None,
) -> list[Detection]:
    import numpy as np

    w, h = sample_size(src_w, src_h)
    sx, sy = src_w / w, src_h / h
    reader = None
    if ocr_engine is not None and words is not None:
        from pas.modules.vietsub.ocr import FrameOCR, SampleGeometry

        geo = SampleGeometry(src_w, src_h, 0, src_h, w, h)
        reader = FrameOCR(ocr_engine, geo, keep=lambda t: True)

    dets: list[Detection] = []
    clip_samples: list[ViolenceSample] = []
    total = max(1, int(duration * SAMPLE_FPS))
    vf = f"fps={SAMPLE_FPS:g},scale={w}:{h}:flags=area,format=bgr24"
    idx = 0
    for bgr in iter_frames(src, tools, vf, w, h, ctx, "kiểm duyệt"):
        t = round(idx / SAMPLE_FPS, 3)
        rgb = None
        if nudity is not None or (violence is not None and idx % CLIP_EVERY == 0):
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        if nudity is not None:
            for label, score, (x0, y0, x1, y1) in nudity(rgb):
                box = _grow((x0 * sx, y0 * sy, x1 * sx, y1 * sy), BOX_MARGIN, src_w, src_h)
                dets.append(Detection(t, "nudity", EXPOSED.get(label, label), score, box, NUDITY_HOLD))
        if violence is not None and idx % CLIP_EVERY == 0:
            score, label = violence(rgb)
            boxes: list[Box] = []
            if score >= VIOLENCE_THRESHOLD:  # chỉ định vị khung bị gắn cờ (tốn ~0,4 s/khung)
                boxes = [(x0 * sx, y0 * sy, x1 * sx, y1 * sy) for x0, y0, x1, y1 in violence.localize(rgb)]
            clip_samples.append((t, score, label, boxes))
        if reader is not None and idx % OCR_EVERY == OCR_OFFSET:
            for line in reader(bgr):
                found = words.find(line.text)
                if found:
                    box = _grow(line.box, BOX_MARGIN, src_w, src_h)
                    dets.append(Detection(t, "text", ", ".join(dict.fromkeys(f.lower() for f in found)),
                                          line.score, box, TEXT_HOLD))
        idx += 1
        if on_progress and idx % 4 == 0:
            on_progress(min(1.0, idx / total))
    dets += violence_runs(clip_samples)
    dets.sort(key=lambda d: d.t)
    if clip_samples:
        top = max(clip_samples, key=lambda s: s[1])
        log.info("CLIP: điểm bạo lực cao nhất %.3f lúc %.1fs (%s)", top[1], top[0], top[2])
    log.info("Kiểm duyệt %d khung mẫu: %s", idx,
             ", ".join(f"{k}={sum(d.kind == k for d in dets)}" for k in KINDS) or "không có")
    return dets


@dataclass
class Interval:
    kind: str
    start: float
    end: float
    labels: list[str]
    score: float


def intervals(dets: list[Detection], duration: float, gap: float = 0.3) -> list[Interval]:
    """Gộp các lần phát hiện cùng loại gần nhau thành khoảng thời gian (dùng cho báo cáo)."""
    out: list[Interval] = []
    for kind in KINDS:
        cur: Interval | None = None
        for d in sorted((d for d in dets if d.kind == kind), key=lambda d: d.t):
            s, e = max(0.0, d.t - d.hold), min(duration, d.t + d.hold)
            if cur and s <= cur.end + gap:
                cur.end = max(cur.end, e)
                cur.score = max(cur.score, d.score)
                if d.label not in cur.labels:
                    cur.labels.append(d.label)
            else:
                cur = Interval(kind, s, e, [d.label], d.score)
                out.append(cur)
    out.sort(key=lambda i: (i.start, i.kind))
    return out
