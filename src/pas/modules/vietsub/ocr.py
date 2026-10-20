"""Tìm phụ đề tiếng Trung in sẵn trên hình.

Cách làm: FFmpeg giải mã video ở vài khung/giây, cắt vùng tìm kiếm, thu nhỏ còn ≤ 960 px ngang và đẩy ảnh thô
(bgr24) qua pipe → RapidOCR (PP-OCRv4, Apache-2.0) dò vùng chữ trên mọi khung, chỉ nhận dạng lại khi vùng chữ
đổi → gom các khung liên tiếp cùng nội dung thành một "sự kiện phụ đề" (chữ, vị trí, thời gian bắt đầu/kết thúc).
"""

from __future__ import annotations

import difflib
import logging
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing
from pas.media.ffmpeg import FFmpegTools
from pas.media.frames import iter_frames

if TYPE_CHECKING:
    import numpy as np

    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

SAMPLE_FPS = 4.0
MAX_SAMPLE_WIDTH = 960
MIN_SCORE = 0.6
# Dòng chữ thấp hơn tỉ lệ này của chiều cao video bị bỏ (nhiễu, chữ quá nhỏ không phải phụ đề).
MIN_LINE_HEIGHT_RATIO = 0.018
SIMILARITY = 0.6
MAX_GAP_FRAMES = 1  # cho phép OCR sót 1 khung giữa một câu
MIN_FRAMES = 2
# Chữ đứng yên lâu hơn mức này coi là logo/watermark: vẫn làm mờ nhưng không dịch.
STATIC_SECONDS = 20.0
# Vùng chữ coi là "không đổi" giữa 2 khung → dùng lại kết quả nhận dạng, không chạy rec lại.
_PIXEL_DIFF = 6.0
_BOX_IOU = 0.7

REGIONS = {"bottom": "Nửa dưới khung hình", "full": "Toàn khung hình"}
INSTALL_HINT = "Cài thư viện OCR (RapidOCR, Apache-2.0, ~15 MB mô hình đi kèm): `uv sync --extra vietsub`"

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (pixel, tọa độ video gốc)


@dataclass
class Line:
    text: str
    score: float
    box: Box


@dataclass
class SubEvent:
    start: float
    end: float
    text: str
    box: Box
    line_height: float
    static: bool = False  # logo/watermark: chỉ làm mờ
    vi: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SubEvent":
        return cls(start=d["start"], end=d["end"], text=d["text"], box=tuple(d["box"]),
                   line_height=d["line_height"], static=d.get("static", False), vi=d.get("vi", ""))


@dataclass(frozen=True)
class SampleGeometry:
    """Vùng tìm kiếm trong video gốc và kích thước ảnh mẫu đưa vào OCR."""
    src_w: int
    src_h: int
    y0: int
    height: int
    out_w: int
    out_h: int

    @property
    def scale(self) -> float:  # pixel mẫu → pixel gốc
        return self.src_w / self.out_w

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale, self.y0 + y * self.scale


def sample_geometry(src_w: int, src_h: int, region: str) -> SampleGeometry:
    y0 = (src_h // 2) & ~1 if region == "bottom" else 0
    height = (src_h - y0) & ~1
    out_w = min(MAX_SAMPLE_WIDTH, src_w) & ~1
    out_h = max(2, round(height * out_w / src_w) & ~1)
    return SampleGeometry(src_w, src_h, y0, height, out_w, out_h)


def _union(a: Box, b: Box) -> Box:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def _v_overlap(a: Box, b: Box) -> float:
    """Tỉ lệ chồng lấn theo chiều dọc so với dòng thấp hơn."""
    inter = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, inter) / max(1e-6, min(a[3] - a[1], b[3] - b[1]))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


# ---------------------------------------------------------------- gom khung → sự kiện


@dataclass
class _Track:
    first: int
    last: int
    readings: list[tuple[str, float]] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)


def group_events(frames: list[tuple[int, list[Line]]], fps: float = SAMPLE_FPS) -> list[SubEvent]:
    """frames: (chỉ số khung, các dòng chữ Trung trên khung đó), theo thứ tự thời gian.

    Mỗi dòng nối vào "track" đang mở nếu cùng chỗ (chồng dọc) và chữ giống nhau ≥ SIMILARITY; track không được
    nối quá MAX_GAP_FRAMES khung thì đóng lại. Sau đó các track xuất hiện/biến mất cùng lúc và nằm sát nhau theo
    chiều dọc (phụ đề 2 dòng) được ghép thành một sự kiện.
    """
    open_tracks: list[_Track] = []
    closed: list[_Track] = []
    for idx, lines in frames:
        still_open = []
        for tr in open_tracks:
            (closed if idx - tr.last > MAX_GAP_FRAMES + 1 else still_open).append(tr)
        open_tracks = still_open
        used: set[int] = set()
        for line in sorted(lines, key=lambda ln: ln.box[1]):
            best, best_sim = None, SIMILARITY
            for i, tr in enumerate(open_tracks):
                if i in used or tr.last == idx:
                    continue
                if _v_overlap(tr.boxes[-1], line.box) < 0.5:
                    continue
                sim = similarity(tr.readings[-1][0], line.text)
                if sim >= best_sim:
                    best, best_sim = i, sim
            if best is None:
                open_tracks.append(_Track(idx, idx, [(line.text, line.score)], [line.box]))
                used.add(len(open_tracks) - 1)
            else:
                tr = open_tracks[best]
                tr.last = idx
                tr.readings.append((line.text, line.score))
                tr.boxes.append(line.box)
                used.add(best)
    closed.extend(open_tracks)

    tracks = [t for t in closed if len(t.readings) >= MIN_FRAMES]
    tracks.sort(key=lambda t: (t.first, _box_of(t)[1]))
    events: list[SubEvent] = []
    merged = [False] * len(tracks)
    for i, tr in enumerate(tracks):
        if merged[i]:
            continue
        group = [tr]
        for j in range(i + 1, len(tracks)):
            other = tracks[j]
            if merged[j] or other.first > tr.first + 1:
                continue
            if abs(other.last - tr.last) <= 1 and _stacked(_box_of(group[-1]), _box_of(other)):
                group.append(other)
                merged[j] = True
        group.sort(key=lambda t: _box_of(t)[1])
        box = _box_of(group[0])
        for g in group[1:]:
            box = _union(box, _box_of(g))
        first = min(g.first for g in group)
        last = max(g.last for g in group)
        heights = sorted(b[3] - b[1] for g in group for b in g.boxes)
        start, end = first / fps, (last + 1) / fps
        events.append(SubEvent(
            start=round(start, 3), end=round(end, 3),
            text=" ".join(_best_reading(g) for g in group),
            box=tuple(round(v, 1) for v in box), line_height=round(heights[len(heights) // 2], 1),
            static=end - start > STATIC_SECONDS,
        ))
    events.sort(key=lambda e: (e.start, e.box[1]))
    return events


def _box_of(tr: _Track) -> Box:
    box = tr.boxes[0]
    for b in tr.boxes[1:]:
        box = _union(box, b)
    return box


def _stacked(upper: Box, lower: Box) -> bool:
    """Hai dòng phụ đề chồng nhau: khoảng cách dọc nhỏ hơn 1 dòng và chồng lấn theo chiều ngang."""
    h = max(upper[3] - upper[1], lower[3] - lower[1])
    gap = lower[1] - upper[3]
    x_overlap = min(upper[2], lower[2]) - max(upper[0], lower[0])
    return -0.5 * h <= gap <= h and x_overlap > 0


def _best_reading(tr: _Track) -> str:
    counts = Counter(t for t, _ in tr.readings)
    best_score: dict[str, float] = {}
    for t, s in tr.readings:
        best_score[t] = max(best_score.get(t, 0.0), s)
    return max(counts, key=lambda t: (counts[t], best_score[t]))


# ---------------------------------------------------------------- OCR thật


def load_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise DependencyMissing("Chưa cài thư viện nhận dạng chữ (RapidOCR).", INSTALL_HINT) from exc
    # det_limit_type 'max': không phóng to dải ảnh dẹt (mặc định 'min' phóng 1280×290 lên 3264×736, chậm 20 lần).
    with _no_spinning():
        return RapidOCR(det_limit_type="max", det_limit_side_len=MAX_SAMPLE_WIDTH)


@contextmanager
def _no_spinning():
    """Tắt luồng chờ kiểu "spin" của onnxruntime cho 3 phiên det/cls/rec của RapidOCR (thư viện không có tùy chọn này).

    Ba phiên chạy nối tiếp, mỗi phiên giữ nguyên bộ luồng quay chờ → giành CPU của nhau: đo trên i5-14400 với video
    60 giây, quét chữ mất 46–57 giây khi bật spin, 6–9 giây khi tắt. Không tìm thấy điểm móc (bản RapidOCR khác) thì
    giữ nguyên hành vi mặc định.
    """
    try:
        from rapidocr_onnxruntime.utils import infer_engine
        cls = infer_engine.OrtInferSession
        orig = cls.__dict__["_init_sess_opts"].__func__
    except (ImportError, AttributeError, KeyError):
        yield
        return

    def init_sess_opts(config):
        opts = orig(config)
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        return opts

    cls._init_sess_opts = staticmethod(init_sess_opts)
    try:
        yield
    finally:
        cls._init_sess_opts = staticmethod(orig)


class FrameOCR:
    """OCR từng khung, nhớ khung trước để bỏ qua bước nhận dạng khi chữ không đổi.

    `keep`: dòng nào được trả về (mặc định: có chữ Trung). Kiểm duyệt dùng `keep=lambda t: True` để đọc mọi chữ.
    """

    def __init__(self, engine, geo: SampleGeometry, keep: Callable[[str], bool] = has_cjk):
        self.engine = engine
        self.geo = geo
        self.keep = keep
        self._prev: list[tuple[Box, "np.ndarray", str, float]] = []  # box mẫu, ảnh xám vùng chữ, chữ, điểm
        self.rec_calls = 0
        self.min_h = MIN_LINE_HEIGHT_RATIO * geo.src_h / geo.scale

    def __call__(self, img: "np.ndarray") -> list[Line]:
        import numpy as np

        boxes, _ = self.engine(img, use_det=True, use_cls=False, use_rec=False)
        current: list[tuple[Box, np.ndarray, str, float]] = []
        lines: list[Line] = []
        h, w = img.shape[:2]
        for quad in boxes or []:
            pts = np.asarray(quad, dtype=np.float32)
            # float Python thuần: tọa độ đi vào JSON trạng thái (float32 của numpy không ghi được).
            x0, y0 = (float(v) for v in pts.min(0))
            x1, y1 = (float(v) for v in pts.max(0))
            if y1 - y0 < self.min_h:  # chữ quá nhỏ: không phải phụ đề
                continue
            pad = max(2, int((y1 - y0) * 0.1))
            cx0, cy0 = max(0, int(x0) - pad), max(0, int(y0) - pad)
            cx1, cy1 = min(w, int(x1) + pad + 1), min(h, int(y1) + pad + 1)
            crop = img[cy0:cy1, cx0:cx1]
            if crop.size == 0:
                continue
            gray = crop.mean(axis=2)
            box: Box = (x0, y0, x1, y1)
            reused = self._reuse(box, gray)
            if reused is None:
                rec, _ = self.engine(crop, use_det=False, use_cls=False, use_rec=True)
                self.rec_calls += 1
                text, score = (rec[0][0], float(rec[0][1])) if rec else ("", 0.0)
            else:
                text, score = reused
            current.append((box, gray, text, score))
            text = text.strip()
            if score >= MIN_SCORE and text and self.keep(text):
                sx0, sy0 = self.geo.to_source(x0, y0)
                sx1, sy1 = self.geo.to_source(x1, y1)
                lines.append(Line(text, round(score, 3), (sx0, sy0, sx1, sy1)))
        self._prev = current
        return lines

    def _reuse(self, box: Box, gray: "np.ndarray") -> tuple[str, float] | None:
        import numpy as np

        for pbox, pgray, text, score in self._prev:
            if _iou(pbox, box) < _BOX_IOU or pgray.shape != gray.shape:
                continue
            if float(np.abs(pgray - gray).mean()) < _PIXEL_DIFF:
                return text, score
        return None


def scan_video(
    src: Path,
    tools: FFmpegTools,
    width: int,
    height: int,
    duration: float,
    region: str,
    ctx: "JobContext | None" = None,
    on_progress: Callable[[float], None] | None = None,
    engine=None,
) -> list[SubEvent]:
    """Đọc toàn bộ video, trả về các sự kiện phụ đề tiếng Trung (tọa độ video gốc)."""
    engine = engine or load_engine()
    geo = sample_geometry(width, height, region)
    ocr = FrameOCR(engine, geo)
    vf = (f"fps={SAMPLE_FPS:g},crop={geo.src_w}:{geo.height}:0:{geo.y0},"
          f"scale={geo.out_w}:{geo.out_h}:flags=area,format=bgr24")
    frames: list[tuple[int, list[Line]]] = []
    total_frames = max(1, int(duration * SAMPLE_FPS))
    idx = 0
    for img in iter_frames(src, tools, vf, geo.out_w, geo.out_h, ctx, "nhận dạng chữ"):
        lines = ocr(img)
        if lines:
            frames.append((idx, lines))
        idx += 1
        if on_progress and idx % 4 == 0:
            on_progress(min(1.0, idx / total_frames))
    events = group_events(frames, SAMPLE_FPS)
    log.info("OCR %d khung, %d lần nhận dạng, %d sự kiện", idx, ocr.rec_calls, len(events))
    return events
