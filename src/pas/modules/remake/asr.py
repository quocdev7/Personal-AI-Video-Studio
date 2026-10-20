"""Nhận dạng giọng nói → transcript có mốc thời gian, bằng faster-whisper (MIT) + Whisper large-v3-turbo chạy CPU.

FFmpeg tách âm thanh 16 kHz mono ra thư mục tạm → Whisper (lọc khoảng lặng VAD) → danh sách câu.
Câu dài bị tách theo dấu câu / mốc thời gian từng từ để vừa làm phụ đề.
"""

from __future__ import annotations

import logging
import os
import re
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput
from pas.media.ffmpeg import FFmpegTools, run_ffmpeg

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

INSTALL_HINT = "Cài thư viện nhận dạng giọng nói (faster-whisper, MIT): `uv sync --extra asr`"
LANGUAGES = {
    "auto": "Tự nhận biết",
    "zh": "Tiếng Trung",
    "en": "Tiếng Anh",
    "ja": "Tiếng Nhật",
    "ko": "Tiếng Hàn",
    "vi": "Tiếng Việt",
}
# Gợi ý văn phong cho Whisper: tiếng Trung ra chữ giản thể có dấu câu (mặc định hay ra phồn thể, không dấu).
_PROMPTS = {"zh": "以下是普通话的句子，使用简体中文和标点符号。"}
MAX_LINE_SECONDS = 8.0
MAX_LINE_CHARS = {"zh": 28, "ja": 28, "ko": 40}
DEFAULT_MAX_CHARS = 84
_BREAK = re.compile(r"[。！？!?；;，,、…]$|[.]$")


@dataclass
class Line:
    id: int
    start: float
    end: float
    text: str
    vi: str = ""
    en: str = ""  # bản dịch trung gian tiếng Anh (bước dịch qua tiếng Anh)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Line":
        return cls(int(d["id"]), float(d["start"]), float(d["end"]), str(d["text"]), str(d.get("vi", "")),
                   str(d.get("en", "")))


def extract_audio(src: Path, tools: FFmpegTools, out: Path, duration: float, ctx: "JobContext | None" = None,
                  on_progress: Callable[[float], None] | None = None) -> Path:
    part = out.with_name(out.name + ".part.wav")
    try:
        run_ffmpeg(tools, ["-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                           "-f", "wav", "-y", str(part)], ctx, duration, on_progress)
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    return out


def read_wav(path: Path):
    """WAV PCM 16-bit mono 16 kHz (do extract_audio tạo) → mảng float32 [-1, 1] cho Whisper.

    Tự đọc thay vì để faster-whisper giải mã bằng PyAV: faster-whisper 1.2 gọi tham số đã bị PyAV ≥ 16 bỏ.
    """
    import numpy as np

    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
                raise ValueError(f"định dạng {w.getnchannels()} kênh, {w.getframerate()} Hz, "
                                 f"{8 * w.getsampwidth()} bit — cần mono 16 kHz 16 bit")
            data = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        raise ExternalToolError(f"Không đọc được âm thanh đã tách {path}: {exc}", "Chạy lại bước 1.") from exc
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def load_model(model_dir: Path):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise DependencyMissing("Chưa cài thư viện nhận dạng giọng nói (faster-whisper).", INSTALL_HINT) from exc
    threads = max(1, (os.cpu_count() or 4) - 2)  # chừa 2 luồng cho giao diện
    try:
        return WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=threads)
    except Exception as exc:  # CTranslate2 báo lỗi bằng RuntimeError chung
        raise ExternalToolError(f"Không nạp được mô hình Whisper ở {model_dir}: {exc}",
                                "File mô hình có thể hỏng. Xóa thư mục đó rồi tải lại ở trang 'Làm lại video'.") from exc


def _split_words(words, lang: str) -> list[tuple[float, float, str]]:
    """Tách một đoạn dài thành các câu ngắn theo dấu câu, giới hạn số ký tự và thời lượng."""
    limit = MAX_LINE_CHARS.get(lang, DEFAULT_MAX_CHARS)
    joiner = "" if lang in MAX_LINE_CHARS else " "
    out: list[tuple[float, float, str]] = []
    cur: list = []

    def flush():
        if cur:
            text = joiner.join(w.word.strip() for w in cur).strip()
            if text:
                out.append((cur[0].start, cur[-1].end, text))
            cur.clear()

    for w in words:
        cur.append(w)
        n = len(joiner.join(x.word.strip() for x in cur))
        seconds = cur[-1].end - cur[0].start
        at_break = bool(_BREAK.search(w.word.strip()))
        if at_break and (n >= limit * 0.4 or seconds >= MAX_LINE_SECONDS):
            flush()  # ngắt ở dấu câu khi câu đã đủ dài
        elif n >= limit * 1.3 or seconds >= MAX_LINE_SECONDS * 1.5:
            flush()  # không có dấu câu: ngắt cứng để phụ đề không quá dài
    flush()
    return out


def transcribe(model, audio: Path, duration: float, language: str, ctx: "JobContext | None" = None,
               on_progress: Callable[[float], None] | None = None) -> tuple[list[Line], str, float]:
    """Trả về (các câu, mã ngôn ngữ, độ tin cậy nhận biết ngôn ngữ)."""
    lang = None if language == "auto" else language
    samples = read_wav(audio)
    if samples.size == 0:
        raise InvalidInput("Video không có tiếng (âm thanh rỗng).", "Chọn video có lời thoại/lời kể.")
    try:
        segments, info = model.transcribe(
            samples, language=lang, beam_size=5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=False, initial_prompt=_PROMPTS.get(lang or ""),
        )
    except Exception as exc:
        raise ExternalToolError(f"Whisper lỗi khi bắt đầu nhận dạng: {exc}", "Xem log tác vụ.") from exc
    detected = info.language
    if lang is None and detected in _PROMPTS:
        # Nhận biết ngôn ngữ xong mới biết là tiếng Trung → chạy lại với gợi ý văn phong (rẻ: chưa giải mã gì).
        segments, info = model.transcribe(
            samples, language=detected, beam_size=5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=False, initial_prompt=_PROMPTS[detected],
        )
    lines: list[Line] = []
    it = iter(segments)
    while True:
        if ctx is not None:
            ctx.checkpoint()
        try:
            seg = next(it)
        except StopIteration:
            break
        except Exception as exc:
            raise ExternalToolError(f"Whisper lỗi khi nhận dạng: {exc}", "Xem log tác vụ.") from exc
        parts = _split_words(seg.words or [], detected) if seg.words else [(seg.start, seg.end, seg.text.strip())]
        for start, end, text in parts:
            text = text.strip()
            if text:
                lines.append(Line(len(lines), round(start, 3), round(max(end, start + 0.2), 3), text))
        if on_progress and duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return lines, detected, float(info.language_probability or 0.0)
