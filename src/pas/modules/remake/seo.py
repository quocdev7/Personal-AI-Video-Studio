"""Thumbnail + SEO: tiêu đề gây tò mò, mô tả, từ khóa/hashtag theo tệp người xem (LLM cục bộ) và 3 ảnh thumbnail.

Từ khóa là đề xuất của mô hình ngôn ngữ dựa trên nội dung — ứng dụng KHÔNG có dữ liệu lượng tìm kiếm thật
(không gọi dịch vụ bên ngoài). Thumbnail lấy từ khung hình nét/sáng/nhiều màu nhất của video gốc, che chữ Trung
cũ, cắt về 1280×720 và in chữ lớn có viền.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError
from pas.media.ffmpeg import CREATE_NO_WINDOW, FFmpegTools
from pas.media.frames import iter_frames
from pas.modules.vietsub.ocr import has_cjk
from pas.modules.vietsub.translate import OllamaClient

if TYPE_CHECKING:
    import numpy as np

    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

THUMB_SIZE = (1280, 720)
THUMB_COUNT = 3
SCORE_SIZE = (320, 180)
MIN_GAP = 15.0  # giây giữa hai khung được chọn (tránh 3 ảnh giống nhau)
FONT_FILES = ("arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf")
PILLOW_HINT = "Cài Pillow (MIT-CMU): `uv sync --extra remake`"
NO_VOLUME_NOTE = ("Lưu ý: từ khóa do mô hình ngôn ngữ cục bộ đề xuất từ nội dung video — KHÔNG có số liệu lượng tìm "
                  "kiếm/cạnh tranh thật. Kiểm tra thêm bằng ô gợi ý tìm kiếm của YouTube/TikTok trước khi dùng.")

SEO_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "thumbnail_texts": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "audience": {"type": "string"},
    },
    "required": ["titles", "thumbnail_texts", "description", "keywords", "hashtags", "audience"],
}


def seo_system() -> str:
    return (
        "Bạn là chuyên gia SEO YouTube/TikTok cho khán giả Việt Nam. Từ kịch bản video, trả về JSON:\n"
        "\"titles\": 5 tiêu đề tiếng Việt khác nhau, 40–70 ký tự, gây tò mò (câu hỏi, điều bất ngờ, con số, kết quả "
        "trước–sau) nhưng ĐÚNG nội dung, không giật tít sai sự thật;\n"
        "\"thumbnail_texts\": 3 cụm chữ in lên thumbnail, mỗi cụm 2–5 từ, mạnh, dễ đọc;\n"
        "\"description\": mô tả 2–4 câu, có từ khóa chính ở câu đầu;\n"
        "\"keywords\": 10–15 từ khóa/cụm từ người Việt hay gõ tìm chủ đề này (từ rộng đến hẹp);\n"
        "\"hashtags\": 5–8 hashtag không dấu cách, bắt đầu bằng #;\n"
        "\"audience\": 1 câu mô tả tệp người xem phù hợp.\n"
        "Chỉ dùng thông tin có trong kịch bản; tiếng Việt có dấu; không emoji."
    )


@dataclass
class SeoResult:
    titles: list[str]
    thumbnail_texts: list[str]
    description: str
    keywords: list[str]
    hashtags: list[str]
    audience: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _strings(value, lo: int, hi: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in value if isinstance(value, list) else []:
        if not isinstance(v, str):
            continue
        t = " ".join(v.split()).strip(" \"'“”")
        if lo <= len(t) <= hi and not has_cjk(t) and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def parse_seo(data: dict) -> SeoResult | None:
    if not isinstance(data, dict):
        return None
    titles = _strings(data.get("titles"), 10, 100)
    keywords = _strings(data.get("keywords"), 2, 60)
    texts = _strings(data.get("thumbnail_texts"), 2, 40)
    tags = []
    for t in _strings(data.get("hashtags"), 2, 40):
        t = "#" + "".join(t.lstrip("#").split())
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    desc = " ".join(str(data.get("description") or "").split())
    audience = " ".join(str(data.get("audience") or "").split())
    if len(titles) < 3 or len(keywords) < 5 or has_cjk(desc):
        return None
    if not texts:  # thiếu chữ thumbnail → lấy tiêu đề ngắn nhất cắt gọn
        texts = [" ".join(min(titles, key=len).split()[:5])]
    return SeoResult(titles[:5], texts[:THUMB_COUNT], desc, keywords[:15], tags[:8], audience)


def generate_seo(client: OllamaClient, script: str, notes: str = "", ctx: "JobContext | None" = None) -> SeoResult:
    user = ("Kịch bản video:\n" + script[:6000]) + (f"\n\nGhi chú của người làm video: {notes}" if notes else "")
    for temp in (0.6, 0.9):
        res = parse_seo(client.chat_json(user, 1200, temp, ctx, seo_system(), SEO_SCHEMA, 8192))
        if res:
            return res
    raise ExternalToolError(f"Mô hình {client.model} không tạo được tiêu đề/từ khóa hợp lệ sau 2 lần thử.",
                            "Bấm 'Chạy lại'. Nếu lặp lại, chọn mô hình lớn hơn trong Cài đặt.")


def seo_text(res: SeoResult, credit: str, voice_credit: str) -> str:
    out = ["TIÊU ĐỀ GỢI Ý"] + [f"{i}. {t}" for i, t in enumerate(res.titles, 1)]
    out += ["", "MÔ TẢ", res.description]
    if credit:
        out += ["", f"Nguồn video gốc: {credit}"]
    if voice_credit:
        out += [voice_credit]
    out += ["", "TỪ KHÓA", ", ".join(res.keywords), "", "HASHTAG", " ".join(res.hashtags),
            "", "TỆP NGƯỜI XEM", res.audience, "", "CHỮ TRÊN THUMBNAIL"] + [f"- {t}" for t in res.thumbnail_texts]
    out += ["", NO_VOLUME_NOTE,
            "Bản quyền: video làm lại vẫn dùng hình ảnh của video gốc — chỉnh sửa KHÔNG làm mất bản quyền của tác giả "
            "gốc. Chỉ đăng khi bạn có quyền sử dụng nội dung đó."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- chọn khung


def frame_score(frame: "np.ndarray") -> float:
    """Nét (phương sai Laplacian) × nhiều màu × độ sáng vừa phải — khung mờ nhòe/tối/chuyển cảnh bị điểm thấp."""
    import numpy as np

    f = frame.astype(np.float32)
    gray = f.mean(axis=2)
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    sharp = float(lap.var())
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    color = float(np.sqrt(rg.var() + yb.var()) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    bright = float(gray.mean())
    exposure = max(0.0, 1.0 - abs(bright - 125.0) / 125.0)
    return (sharp ** 0.5) * (1.0 + color / 40.0) * (0.3 + exposure)


def pick_times(scores: list[tuple[float, float]], count: int = THUMB_COUNT, gap: float = MIN_GAP,
               skip: float = 1.0) -> list[float]:
    """Các thời điểm điểm cao nhất, cách nhau ≥ gap (bỏ `skip` giây đầu/cuối hay là màn đen/logo)."""
    if not scores:
        return []
    end = scores[-1][0]
    pool = sorted([s for s in scores if skip <= s[0] <= end - skip] or scores, key=lambda x: -x[1])
    out: list[float] = []
    for g in (gap, gap / 3, 0.0):
        for t, _ in pool:
            if len(out) >= count:
                break
            if all(abs(t - o) >= g for o in out) and t not in out:
                out.append(t)
        if len(out) >= count:
            break
    return sorted(out)


def score_frames(src: Path, tools: FFmpegTools, duration: float, ctx: "JobContext | None" = None,
                 on_progress: Callable[[float], None] | None = None) -> list[tuple[float, float]]:
    w, h = SCORE_SIZE
    vf = f"fps=1,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    out = []
    for i, frame in enumerate(iter_frames(src, tools, vf, w, h, ctx, "chọn khung thumbnail")):
        out.append((i + 0.5, frame_score(frame)))
        if on_progress and duration > 0:
            on_progress(min(1.0, i / duration))
    return out


def grab_frame(src: Path, tools: FFmpegTools, t: float, out: Path) -> Path:
    part = out.with_name(out.stem + ".part.png")
    cmd = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
           "-frames:v", "1", "-y", str(part)]
    proc = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
    if proc.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        raise ExternalToolError(f"FFmpeg không lấy được khung hình tại {t:.1f}s: "
                                f"{proc.stderr.decode('utf-8', 'replace')[-300:]}", "Xem log tác vụ.")
    os.replace(part, out)
    return out


# ---------------------------------------------------------------- vẽ thumbnail


def find_font(fonts_dir: Path | None = None) -> Path:
    base = fonts_dir or Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in FONT_FILES:
        if (base / name).is_file():
            return base / name
    raise DependencyMissing(f"Không tìm thấy phông chữ đậm có dấu tiếng Việt ({', '.join(FONT_FILES)}) trong {base}.",
                            "Cài lại phông Arial/Segoe UI của Windows.")


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_thumbnail(frame_png: Path, text: str, out: Path, font_path: Path,
                     blur_boxes: list[tuple[float, float, float, float]] = ()) -> Path:
    """Ảnh 1280×720: cắt giữa, tăng tương phản/màu, che chữ cũ, dải tối dưới cùng + chữ vàng/trắng viền đen."""
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    except ImportError as exc:
        raise DependencyMissing("Chưa cài Pillow để vẽ thumbnail.", PILLOW_HINT) from exc

    W, H = THUMB_SIZE
    with Image.open(frame_png) as im:
        img = im.convert("RGB")
    for x0, y0, x1, y1 in blur_boxes:  # tọa độ khung gốc
        box = (max(0, int(x0)), max(0, int(y0)), min(img.width, int(x1 + 1)), min(img.height, int(y1 + 1)))
        if box[2] - box[0] > 4 and box[3] - box[1] > 4:
            img.paste(img.crop(box).filter(ImageFilter.GaussianBlur(max(6, (box[3] - box[1]) // 2))), box[:2])
    scale = max(W / img.width, H / img.height)
    img = img.resize((max(W, round(img.width * scale)), max(H, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.25)
    img = ImageEnhance.Sharpness(img).enhance(1.3)

    # dải tối dần ở nửa dưới để chữ nổi trên mọi nền
    shade = Image.new("L", (1, H))
    for y in range(H):
        shade.putpixel((0, y), int(200 * max(0.0, (y - H * 0.45) / (H * 0.55)) ** 1.4))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade.resize((W, H)))

    draw = ImageDraw.Draw(img)
    text = text.upper()
    size = 132
    while size > 48:
        font = ImageFont.truetype(str(font_path), size)
        lines = _wrap(draw, text, font, int(W * 0.9))
        line_h = int(size * 1.12)
        if len(lines) <= 2 and line_h * len(lines) <= H * 0.42:
            break
        size -= 8
    y = H - int(H * 0.06) - line_h * len(lines)
    for i, line in enumerate(lines):
        tw = draw.textlength(line, font=font)
        fill = (255, 221, 0) if i == 0 else (255, 255, 255)
        draw.text(((W - tw) / 2, y), line, font=font, fill=fill, stroke_width=max(4, size // 14),
                  stroke_fill=(0, 0, 0))
        y += line_h
    part = out.with_name(out.stem + ".part.jpg")
    img.save(part, "JPEG", quality=92, optimize=True)
    os.replace(part, out)
    return out
