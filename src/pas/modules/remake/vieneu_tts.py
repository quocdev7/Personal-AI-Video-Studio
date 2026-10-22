"""Giọng đọc VieNeu-TTS v3 Turbo (Apache-2.0) — chạy ONNX trên CPU, 48 kHz: giọng có sẵn hoặc nhân bản từ file mẫu.

Mọi file mô hình nạp từ thư mục models của ứng dụng (đã tải + kiểm SHA-256 qua `model.download`). vieneu 3.8.3 không
cho truyền thư mục codec qua API công khai nên `load` đưa `codec_dir` vào engine ONNX lúc khởi tạo, đồng thời chặn mọi
lượt tải ngầm từ Hugging Face: thiếu file thì báo lỗi, không tự tải.
File mẫu của người dùng chỉ được đọc: ffmpeg chuyển sang WAV tạm trong thư mục tác vụ rồi mới đưa cho mô hình.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput
from pas.media.ffmpeg import FFmpegTools, probe, run_ffmpeg
from pas.modules.remake import tts

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

KEY = "vieneu"  # giá trị tham số `voice` của bước 2
INSTALL_HINT = "Cài thư viện giọng đọc VieNeu (Apache-2.0): `uv sync --extra tts`"
SAMPLE_RATE = 48_000
MIN_SAMPLE = 3.0  # giây; mẫu ngắn hơn nhân bản không ổn định
MAX_SAMPLE = 8.0  # mô hình chỉ dùng tối đa 8 giây — mẫu dài hơn lấy 8 giây đầu
SAMPLE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
                     ".mp4", ".mkv", ".mov", ".webm", ".avi"}
CLONE_NOTE = ("Nhân bản giọng: bạn phải có quyền dùng giọng trong file mẫu (giọng của chính bạn, hoặc được người đó / "
              "dịch vụ tạo giọng cho phép). Không nhân bản giọng người thật khi chưa được phép, không dùng để giả mạo.")

_load_lock = threading.Lock()


@dataclass(frozen=True)
class Preset:
    name: str
    description: str  # "Nam · Bắc · Phong cách tự nhiên"


def available() -> bool:
    return importlib.util.find_spec("vieneu") is not None


@lru_cache(maxsize=1)
def presets() -> tuple[Preset, ...]:
    """Giọng có sẵn đi kèm thư viện (Apache-2.0, tác giả xác nhận người đọc đồng ý) — giọng nổi bật xếp trước."""
    spec = importlib.util.find_spec("vieneu")
    if spec is None or not spec.submodule_search_locations:
        return ()
    path = Path(next(iter(spec.submodule_search_locations))) / "assets" / "voices_v3_turbo.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))["presets"]
    except (OSError, ValueError, KeyError):
        return ()
    order = sorted(data.items(), key=lambda kv: (kv[1].get("featured") or 999))
    return tuple(Preset(name, v.get("description", "")) for name, v in order if v.get("speaker_emb"))


def validate_choice(preset, sample) -> None:
    """Đúng một trong hai: tên giọng có sẵn hoặc file mẫu để nhân bản."""
    if sample:
        path = Path(str(sample))
        if not path.is_file():
            raise InvalidInput(f"Không tìm thấy file giọng mẫu: {path}", "Chọn lại file mẫu (3–8 giây, một người nói).")
        if path.suffix.lower() not in SAMPLE_EXTENSIONS:
            raise InvalidInput(f"File giọng mẫu không phải âm thanh/video: {path.name}",
                               "Dùng file .wav, .mp3, .m4a, .flac… dài 3–8 giây.")
        return
    if preset not in {p.name for p in presets()}:
        raise InvalidInput(f"Giọng VieNeu không hợp lệ: {preset!r}", "Chọn giọng trong danh sách, hoặc chọn file mẫu.")


@contextlib.contextmanager
def _offline(codec_dir: Path):
    """Trong lúc khởi tạo: đưa thư mục codec cục bộ vào engine ONNX và chặn hf_hub_download."""
    import huggingface_hub
    from vieneu._v3_turbo_engine import onnx_runtime_lite as orl

    base = orl.OnnxV3LiteEngine
    real_download = huggingface_hub.hf_hub_download

    class LocalEngine(base):
        def __init__(self, *args, **kwargs):
            kwargs["codec_dir"] = str(codec_dir)
            super().__init__(*args, **kwargs)

    def blocked(repo_id, filename, *args, **kwargs):
        raise ExternalToolError(f"VieNeu cần file {filename} chưa có trong thư mục mô hình.",
                                "Tải lại mô hình VieNeu ở trang 'Làm lại video' → bước 2.")

    with _load_lock:
        orl.OnnxV3LiteEngine = LocalEngine
        huggingface_hub.hf_hub_download = blocked
        try:
            yield
        finally:
            orl.OnnxV3LiteEngine = base
            huggingface_hub.hf_hub_download = real_download


def load(root: Path, threads: int = 0):
    """Nạp mô hình từ `root` (thư mục `models.VIENEU_DIR`). Mất vài giây."""
    if not available():
        raise DependencyMissing("Chưa cài thư viện giọng đọc VieNeu.", INSTALL_HINT)
    try:
        from vieneu import Vieneu

        with _offline(root / "codec"):
            return Vieneu(mode="v3turbo", backend="onnx", backbone_repo=str(root),
                          onnx_dir=str(root / "onnx_update"), threads=threads)
    except ExternalToolError:
        raise
    except Exception as exc:  # onnxruntime/numpy báo lỗi bằng nhiều kiểu khác nhau
        raise ExternalToolError(f"Không nạp được mô hình VieNeu: {exc}",
                                "File mô hình có thể hỏng. Xóa thư mục vieneu-v3-turbo trong models rồi tải lại.") from exc


def prepare_sample(src: Path, tools: FFmpegTools, out: Path, ctx: "JobContext | None" = None) -> float:
    """File mẫu (bất kỳ định dạng ffmpeg đọc được) → WAV mono 48 kHz, tối đa MAX_SAMPLE giây. Trả về độ dài dùng."""
    info = probe(src, tools)
    if not info.has_audio:
        raise InvalidInput(f"File giọng mẫu không có âm thanh: {src.name}", "Chọn file có tiếng người nói.")
    if info.duration < MIN_SAMPLE:
        raise InvalidInput(f"File giọng mẫu quá ngắn ({info.duration:.1f} giây, cần ít nhất {MIN_SAMPLE:.0f} giây).",
                           "Dùng đoạn 3–8 giây, một người nói rõ, không nhạc nền.")
    used = min(info.duration, MAX_SAMPLE)
    part = out.with_name(out.name + ".part.wav")
    try:
        run_ffmpeg(tools, ["-i", str(src), "-map", "0:a:0", "-vn", "-t", f"{MAX_SAMPLE}", "-ac", "1",
                           "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", "-y", str(part)], ctx, used)
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return used


def synthesize(engine, text: str, tone: tts.Tone, out: Path, ctx: "JobContext | None" = None,
               on_progress: Callable[[float], None] | None = None, preset: str | None = None,
               sample: Path | None = None) -> dict:
    """Đọc kịch bản bằng giọng có sẵn `preset` hoặc giọng nhân bản từ `sample` (WAV do prepare_sample tạo)."""
    import numpy as np

    if sample is not None:
        try:
            emb, codes = engine.encode_reference(str(sample), denoise=True)
        except Exception as exc:
            raise ExternalToolError(f"Không lấy được đặc trưng giọng từ file mẫu: {exc}",
                                    "Dùng đoạn 3–8 giây, một người nói rõ, ít tiếng ồn.") from exc
        voice = {"speaker_emb": emb, "codes": codes}
    else:
        voice = preset

    def speak(sentence: str) -> bytes:
        try:
            wav = engine.infer(sentence, voice=voice)
        except Exception as exc:
            raise ExternalToolError(f"VieNeu lỗi khi đọc câu {sentence[:60]!r}: {exc}",
                                    "Sửa câu đó (bỏ ký tự lạ) rồi chạy lại.") from exc
        pcm = np.clip(np.asarray(wav, dtype=np.float32).reshape(-1), -1.0, 1.0)
        return (pcm * 32767.0).astype("<i2").tobytes()

    return tts.render(speak, SAMPLE_RATE, text, tone, out, ctx, on_progress)


def credit(preset: str | None, sample: Path | None) -> str:
    who = "giọng nhân bản từ file mẫu" if sample else f"giọng {preset}"
    return f"Giọng đọc: VieNeu-TTS v3 Turbo, {who} (Apache-2.0)"
