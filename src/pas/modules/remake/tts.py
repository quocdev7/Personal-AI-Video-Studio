"""Giọng đọc mới bằng Piper (GPL-3.0, chạy cục bộ CPU) — đọc từng câu, ghi mốc thời gian từng câu/đoạn.

Mốc thời gian (voice.json) dùng để bước dựng video đặt phụ đề đúng câu và chọn cảnh khớp lời.
"Tông giọng" chỉ chỉnh tốc độ, độ biểu cảm và khoảng nghỉ — Piper không đổi được cảm xúc/giới tính của một giọng.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

INSTALL_HINT = "Cài thư viện giọng đọc (piper-tts, GPL-3.0): `uv sync --extra tts`"
MAX_CHARS = 20_000
SENTENCE_GAP = 0.12  # nghỉ giữa hai câu trong một đoạn (giây)
TAIL = 0.4


@dataclass(frozen=True)
class Tone:
    label: str
    length_scale: float  # >1 chậm hơn
    noise_scale: float  # biến thiên ngữ điệu
    noise_w_scale: float  # biến thiên độ dài âm
    pause: float  # nghỉ giữa hai đoạn (giây)


TONES: dict[str, Tone] = {
    "natural": Tone("Tự nhiên", 1.0, 0.667, 0.8, 0.35),
    "fast": Tone("Nhanh, dồn dập (video ngắn)", 0.85, 0.6, 0.8, 0.2),
    "calm": Tone("Chậm, điềm tĩnh (tài liệu)", 1.15, 0.5, 0.6, 0.55),
    "expressive": Tone("Nhiều ngữ điệu (kể chuyện)", 1.0, 0.8, 1.0, 0.4),
}

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+")
_ALLOWED_PUNCT = set(".,!?…:;-–—'\"“”‘’()%/")


def clean_text(text: str) -> str:
    """Bỏ emoji/ký hiệu lạ mà Piper đọc sai hoặc bỏ lửng; giữ chữ, số, dấu câu thông dụng."""
    text = unicodedata.normalize("NFC", text)
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch.isspace():
            out.append(" " if ch != "\n" else "\n")
        elif cat[0] in "LN" or cat == "Mn" or ch in _ALLOWED_PUNCT:
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"[ \t]+", " ", "".join(out))


def paragraphs(text: str) -> list[str]:
    """Đoạn = khối cách nhau bởi dòng trống; văn bản không có dòng trống thì mỗi dòng là một đoạn."""
    text = clean_text(text).replace("\r", "")
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    return [" ".join(b.split()) for b in blocks if b.strip() and any(c.isalnum() for c in b)]


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(paragraph) if any(c.isalnum() for c in s)]


def validate_text(text) -> str:
    if not isinstance(text, str) or not paragraphs(text):
        raise InvalidInput("Chưa có kịch bản để đọc.", "Dán kịch bản vào ô văn bản (hoặc chạy bước 1 để tạo).")
    if len(text) > MAX_CHARS:
        raise InvalidInput(f"Kịch bản quá dài ({len(text):,} ký tự, tối đa {MAX_CHARS:,}).",
                           "Chia thành nhiều video ngắn hơn.")
    return text


def load_voice(onnx: Path):
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise DependencyMissing("Chưa cài thư viện giọng đọc (piper-tts).", INSTALL_HINT) from exc
    try:
        return PiperVoice.load(str(onnx))
    except Exception as exc:  # onnxruntime báo lỗi bằng nhiều kiểu khác nhau
        raise ExternalToolError(f"Không nạp được giọng đọc {onnx.name}: {exc}",
                                "File giọng có thể hỏng. Xóa nó trong thư mục models rồi tải lại.") from exc


def missing_phonemes(voice, text: str) -> set[str]:
    """Âm vị mà espeak-ng sinh ra nhưng giọng không có (Piper sẽ bỏ qua → đọc sai). Ví dụ vivos thiếu thanh 2/6."""
    idmap = voice.config.phoneme_id_map
    missing: set[str] = set()
    for sent in voice.phonemize(text):
        missing.update(p for p in sent if p not in idmap)
    return missing


def synthesize(voice, text: str, tone: Tone, speaker: int | None, out: Path, ctx: "JobContext | None" = None,
               on_progress: Callable[[float], None] | None = None) -> dict:
    """Ghi WAV mono 16-bit tại `out` (qua file .part). Trả về {'sample_rate','duration','segments':[...]}."""
    from piper import SynthesisConfig

    paras = [(p, sentences(p)) for p in paragraphs(text)]
    missing = missing_phonemes(voice, "\n".join(p for p, _ in paras))
    if missing:
        log.warning("Giọng này không có %d âm vị/thanh điệu (%s) nên sẽ đọc sai một số từ — nên dùng giọng vais1000.",
                    len(missing), " ".join(sorted(missing)))
    total = sum(len(s) for _, s in paras) or 1
    cfg = SynthesisConfig(speaker_id=speaker, length_scale=tone.length_scale, noise_scale=tone.noise_scale,
                          noise_w_scale=tone.noise_w_scale, normalize_audio=True, volume=1.0)
    rate = int(voice.config.sample_rate)
    part = out.with_name(out.name + ".part")
    segments: list[dict] = []
    frames = 0
    done = 0

    def silence(seconds: float) -> bytes:
        return b"\x00\x00" * int(round(seconds * rate))

    try:
        with wave.open(str(part), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            for pi, (ptext, sents) in enumerate(paras):
                if pi:
                    gap = silence(tone.pause)
                    w.writeframes(gap)
                    frames += len(gap) // 2
                seg = {"index": pi, "text": ptext, "start": frames / rate, "sentences": []}
                for si, stext in enumerate(sents):
                    if ctx is not None:
                        ctx.checkpoint()
                    if si:
                        gap = silence(SENTENCE_GAP)
                        w.writeframes(gap)
                        frames += len(gap) // 2
                    start = frames / rate
                    try:
                        chunks = list(voice.synthesize(stext, cfg))
                    except Exception as exc:
                        raise ExternalToolError(f"Piper lỗi khi đọc câu {stext[:60]!r}: {exc}",
                                                "Sửa câu đó (bỏ ký tự lạ) rồi chạy lại.") from exc
                    for ch in chunks:
                        if ch.sample_rate != rate or ch.sample_channels != 1 or ch.sample_width != 2:
                            raise ExternalToolError(
                                f"Piper trả âm thanh định dạng lạ ({ch.sample_rate} Hz, {ch.sample_channels} kênh).",
                                "File giọng không đúng phiên bản. Tải lại giọng đọc.")
                        data = ch.audio_int16_bytes
                        w.writeframes(data)
                        frames += len(data) // 2
                    if frames / rate - start < 0.05:
                        log.warning("Piper không đọc ra tiếng cho câu: %r", stext)
                    seg["sentences"].append({"text": stext, "start": round(start, 3), "end": round(frames / rate, 3)})
                    done += 1
                    if on_progress:
                        on_progress(done / total)
                seg["start"] = round(seg["start"], 3)
                seg["end"] = round(frames / rate, 3)
                segments.append(seg)
            tail = silence(TAIL)
            w.writeframes(tail)
            frames += len(tail) // 2
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    return {"sample_rate": rate, "duration": round(frames / rate, 3), "segments": segments}


def _norm(text: str) -> str:
    return "".join(c for c in clean_text(text).lower() if c.isalnum())


def match_script(segments: list[dict], script: list[dict]) -> None:
    """Gắn `script_id` (đoạn kịch bản bước 1 → biết cảnh nguồn) cho từng đoạn giọng đọc.

    Khớp theo nội dung (người dùng có thể sửa vài đoạn); nếu số đoạn bằng nhau thì đoạn chưa khớp lấy theo thứ tự.
    Đoạn không khớp được để None — bước dựng chia cảnh theo tỉ lệ thời gian.
    """
    by_text = {_norm(p["text"]): p["id"] for p in script}
    used: set[int] = set()
    for seg in segments:
        sid = by_text.get(_norm(seg["text"]))
        seg["script_id"] = sid if sid is not None and sid not in used else None
        if seg["script_id"] is not None:
            used.add(seg["script_id"])
    if len(segments) == len(script):
        for seg, part in zip(segments, script):
            if seg["script_id"] is None and part["id"] not in used:
                seg["script_id"] = part["id"]
                used.add(part["id"])
