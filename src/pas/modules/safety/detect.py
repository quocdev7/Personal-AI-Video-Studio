"""Bộ nhận diện: khỏa thân (NudeNet 320n, YOLOv8 — có vị trí) và bạo lực/máu me (CLIP zero-shot — điểm cả khung,
định vị bằng cửa sổ trượt).

Cả hai chạy bằng onnxruntime trên CPU, ảnh đầu vào là numpy RGB uint8. Tiền/hậu xử lý NudeNet viết lại theo
nudenet 3.4.2 (MIT) — cho kết quả như thư viện gốc — nhưng không cần cv2 (OpenCV).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Thứ tự lớp đầu ra của 320n.onnx (nudenet 3.4.2).
NUDENET_LABELS = [
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED",
    "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "ANUS_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED",
]
# Chỉ che các bộ phận lộ mà tiêu chuẩn cộng đồng của Facebook cấm (ngực nam lộ không bị cấm).
EXPOSED = {
    "FEMALE_BREAST_EXPOSED": "ngực nữ lộ",
    "FEMALE_GENITALIA_EXPOSED": "bộ phận sinh dục nữ lộ",
    "MALE_GENITALIA_EXPOSED": "bộ phận sinh dục nam lộ",
    "BUTTOCKS_EXPOSED": "mông lộ",
    "ANUS_EXPOSED": "hậu môn lộ",
}
NUDENET_INPUT = 320
NMS_IOU = 0.45

# CLIP: điểm "nguy hiểm" = tổng xác suất (softmax trên mọi câu mô tả) của các câu trong UNSAFE. Danh sách SAFE đủ đa
# dạng để cảnh đông người/thể thao/vui chơi không bị kéo về phía bạo lực. Đổi danh sách → chạy lại
# scripts/build_clip_prompts.py để tạo clip_prompts.npy.
UNSAFE_PROMPTS = [
    "a photo of blood and gore",
    "a photo of a bloody injured person",
    "a photo of a violent fight",
    "a photo of people punching and beating someone",
    "a photo of a person aiming a gun",
    "a photo of a person threatening with a knife",
    "a photo of a dead body",
]
UNSAFE_LABELS = ["máu me", "người bị thương chảy máu", "đánh nhau", "đánh đập", "chĩa súng", "đe dọa bằng dao", "xác chết"]
SAFE_PROMPTS = [
    "a photo of people", "a photo of a landscape", "a photo of food", "a photo of a sports game",
    "a photo of a person talking", "a photo of a city street", "a photo of an animal", "a photo of a room",
    "a photo of text", "a photo of a party", "a photo of makeup", "a photo of people playing in the water",
    "a photo of a crowd of people", "a photo of people walking", "a photo of children playing",
    "a photo of people dancing",
]
PROMPTS_FILE = Path(__file__).with_name("clip_prompts.npy")
CLIP_SIZE = 224
CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)
CLIP_LOGIT_SCALE = 100.0

Box = tuple[float, float, float, float]


def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    # Nhiều mô hình chạy xen kẽ trên cùng CPU (NudeNet, CLIP, OCR): luồng chờ kiểu "spin" của onnxruntime giành CPU
    # của nhau làm chậm vài lần → tắt.
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def nms(boxes: "np.ndarray", scores: "np.ndarray", iou: float = NMS_IOU) -> list[int]:
    """Non-maximum suppression tham lam, không phân lớp (giống cv2.dnn.NMSBoxes mà NudeNet dùng). boxes: x0,y0,x1,y1."""
    import numpy as np

    order = list(np.argsort(-scores))
    keep: list[int] = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order:
        i = order.pop(0)
        keep.append(int(i))
        if not order:
            break
        rest = np.array(order)
        ix = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        iy = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        inter = ix * iy
        union = areas[i] + areas[rest] - inter
        overlap = np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)
        order = [int(j) for j, o in zip(rest, overlap) if o <= iou]
    return keep


def nudenet_postprocess(output: "np.ndarray", square: int, threshold: float,
                        frame_w: int, frame_h: int) -> list[tuple[str, float, Box]]:
    """output: đầu ra thô (1, 22, N). Trả về (lớp, điểm, hộp pixel trên khung) cho mọi lớp có điểm ≥ threshold."""
    import numpy as np

    rows = np.transpose(np.squeeze(output, 0))  # N × (4 + số lớp)
    cls_scores = rows[:, 4:]
    cls = cls_scores.argmax(1)
    score = cls_scores[np.arange(len(rows)), cls]
    sel = score >= threshold
    if not sel.any():
        return []
    rows, cls, score = rows[sel], cls[sel], score[sel]
    f = square / NUDENET_INPUT
    cx, cy, w, h = (rows[:, k] * f for k in range(4))
    # Cắt hộp vào khung TRƯỚC khi NMS, như nudenet.
    boxes = np.stack([np.clip(cx - w / 2, 0, frame_w), np.clip(cy - h / 2, 0, frame_h),
                      np.clip(cx + w / 2, 0, frame_w), np.clip(cy + h / 2, 0, frame_h)], axis=1)
    out = []
    for i in nms(boxes, score):
        x0, y0, x1, y1 = (float(v) for v in boxes[i])
        if x1 > x0 and y1 > y0:
            out.append((NUDENET_LABELS[int(cls[i])], round(float(score[i]), 3), (x0, y0, x1, y1)))
    return out


def resize_bilinear(img: "np.ndarray", size: int) -> "np.ndarray":
    """Như cv2.resize(INTER_LINEAR) — không khử răng cưa như PIL — để khớp cách NudeNet chuẩn bị ảnh khi huấn luyện."""
    import numpy as np

    h, w = img.shape[:2]

    def axis(n: int):
        c = np.clip((np.arange(size) + 0.5) * n / size - 0.5, 0, n - 1)
        i0 = np.floor(c).astype(np.int64)
        i1 = np.minimum(i0 + 1, n - 1)
        return i0, i1, (c - i0).astype(np.float32)

    y0, y1, fy = axis(h)
    x0, x1, fx = axis(w)
    fy, fx = fy[:, None, None], fx[None, :, None]
    rows = img[y0].astype(np.float32) * (1 - fy) + img[y1].astype(np.float32) * fy  # tách hai chiều: nhanh hơn
    out = rows[:, x0] * (1 - fx) + rows[:, x1] * fx
    return np.clip(np.rint(out), 0, 255).astype(img.dtype) if img.dtype == np.uint8 else out


def nudenet_input(rgb: "np.ndarray") -> "np.ndarray":
    """Ảnh RGB uint8 → tensor (1, 3, 320, 320) giống hệt nudenet 3.4.2 (đã kiểm bằng test so với thư viện gốc)."""
    import numpy as np

    h, w = rgb.shape[:2]
    square = max(h, w)
    padded = np.zeros((square, square, 3), dtype=np.uint8)  # NudeNet đệm phải/dưới bằng màu đen
    # nudenet gọi cvtColor(RGBA2BGR) trên ảnh 3 kênh (= đảo R↔B) rồi blobFromImage(swapRB=True) đảo lần nữa, nên
    # mô hình thực tế nhận BGR. Ngưỡng của NudeNet được chỉnh theo hành vi đó → ta cũng đưa BGR.
    padded[:h, :w] = rgb[:, :, ::-1]
    return (resize_bilinear(padded, NUDENET_INPUT) / 255.0).astype(np.float32).transpose(2, 0, 1)[None]


class NudityDetector:
    def __init__(self, model: Path, threshold: float):
        self.session = _session(model)
        self.input_name = self.session.get_inputs()[0].name
        self.threshold = threshold

    def detect_all(self, rgb: "np.ndarray", threshold: float | None = None) -> list[tuple[str, float, Box]]:
        h, w = rgb.shape[:2]
        square = max(h, w)
        out = self.session.run(None, {self.input_name: nudenet_input(rgb)})[0]
        return nudenet_postprocess(out, square, self.threshold if threshold is None else threshold, w, h)

    def __call__(self, rgb: "np.ndarray") -> list[tuple[str, float, Box]]:
        return [d for d in self.detect_all(rgb) if d[0] in EXPOSED]


def clip_pixels(rgb: "np.ndarray") -> "np.ndarray":
    """Tiền xử lý CLIP: cạnh ngắn → 224 (bicubic), cắt giữa 224×224, chuẩn hóa, NCHW."""
    import numpy as np
    from PIL import Image

    img = Image.fromarray(rgb)
    w, h = img.size
    s = CLIP_SIZE / min(w, h)
    img = img.resize((max(CLIP_SIZE, round(w * s)), max(CLIP_SIZE, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - CLIP_SIZE) // 2, (h - CLIP_SIZE) // 2
    img = img.crop((left, top, left + CLIP_SIZE, top + CLIP_SIZE))
    x = (np.asarray(img, dtype=np.float32) / 255.0 - np.array(CLIP_MEAN, np.float32)) / np.array(CLIP_STD, np.float32)
    return x.transpose(2, 0, 1)[None]


def violence_score(image_embed: "np.ndarray", text_embeds: "np.ndarray") -> tuple[float, int]:
    """(tổng xác suất các câu UNSAFE, chỉ số câu UNSAFE cao nhất)."""
    import numpy as np

    v = image_embed / max(float(np.linalg.norm(image_embed)), 1e-9)
    logits = CLIP_LOGIT_SCALE * (text_embeds @ v)
    p = np.exp(logits - logits.max())
    p /= p.sum()
    n = len(UNSAFE_PROMPTS)
    return float(p[:n].sum()), int(p[:n].argmax())


class ViolenceClassifier:
    def __init__(self, model: Path, threshold: float, prompts_file: Path = PROMPTS_FILE):
        import numpy as np

        text = np.load(prompts_file)
        if text.shape[0] != len(UNSAFE_PROMPTS) + len(SAFE_PROMPTS):
            raise ValueError(f"{prompts_file.name} có {text.shape[0]} câu, cần {len(UNSAFE_PROMPTS) + len(SAFE_PROMPTS)}")
        self.text = text.astype(np.float32)
        self.session = _session(model)
        self.threshold = threshold

    def __call__(self, rgb: "np.ndarray") -> tuple[float, str]:
        embed = self.session.run(["image_embeds"], {"pixel_values": clip_pixels(rgb)})[0][0]
        score, top = violence_score(embed, self.text)
        return round(score, 3), UNSAFE_LABELS[top]

    def localize(self, rgb: "np.ndarray") -> list[Box]:
        """Vùng cần che trên khung đã bị gắn cờ bạo lực (pixel của rgb). Chấm điểm từng cửa sổ vuông trượt (một lượt
        batch, ~0,4 s/khung trên CPU) rồi chọn bằng select_windows — không bao giờ trả về cả khung."""
        import numpy as np

        h, w = rgb.shape[:2]
        wins = sliding_windows(w, h)
        batch = np.concatenate([clip_pixels(rgb[y0:y1, x0:x1]) for x0, y0, x1, y1 in wins])
        embeds = self.session.run(["image_embeds"], {"pixel_values": batch})[0]
        scores = [violence_score(e, self.text)[0] for e in embeds]
        return [tuple(float(v) for v in b) for b in select_windows(wins, scores, w, h)]


# Định vị bạo lực: CLIP chỉ cho điểm cả ảnh, nên chấm từng cửa sổ vuông cạnh = 40% cạnh ngắn (bước nửa cửa sổ) và che
# các cửa sổ gần điểm cao nhất. Thử trên ảnh mẫu: ngưỡng tuyệt đối làm gần như mọi cửa sổ của cảnh bạo lực rõ đều vượt
# (= lại che cả khung), nên chọn tương đối theo đỉnh và giới hạn tổng diện tích.
WINDOW_FRAC = 0.4
WINDOW_MARGIN = 0.1  # giữ cửa sổ có điểm ≥ điểm cao nhất − 0,1
WINDOW_MAX_AREA = 0.35  # tổng vùng che ≤ 35% khung (cửa sổ cao nhất luôn được giữ)


def sliding_windows(w: int, h: int, frac: float = WINDOW_FRAC) -> list[tuple[int, int, int, int]]:
    import math

    import numpy as np

    side = max(1, round(min(w, h) * frac))
    stride = side / 2
    nx = max(1, math.ceil((w - side) / stride) + 1)
    ny = max(1, math.ceil((h - side) / stride) + 1)
    xs = np.linspace(0, w - side, nx).round().astype(int)
    ys = np.linspace(0, h - side, ny).round().astype(int)
    return [(int(x), int(y), int(x) + side, int(y) + side) for y in ys for x in xs]


def select_windows(wins: list[tuple[int, int, int, int]], scores, w: int, h: int,
                   margin: float = WINDOW_MARGIN, max_area: float = WINDOW_MAX_AREA) -> list[tuple[int, int, int, int]]:
    """Cửa sổ cao điểm nhất, rồi lần lượt các cửa sổ có điểm ≥ đỉnh − margin miễn tổng diện tích (hợp) ≤ max_area."""
    import numpy as np

    if not wins:
        return []
    scores = np.asarray(scores, dtype=float)
    order = np.argsort(-scores, kind="stable")
    top = scores[order[0]]
    covered = np.zeros((h, w), dtype=bool)
    chosen: list[tuple[int, int, int, int]] = []
    for i in order:
        if chosen and scores[i] < top - margin:
            break
        x0, y0, x1, y1 = wins[i]
        trial = covered.copy()
        trial[y0:y1, x0:x1] = True
        if chosen and trial.mean() > max_area:
            continue
        covered = trial
        chosen.append(wins[i])
    return chosen
