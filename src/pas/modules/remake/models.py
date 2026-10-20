"""Mô hình của "Làm lại video" — tải qua tác vụ dùng chung `model.download`, chỉ khi người dùng bấm tải và đồng ý.

URL cố định theo commit; kích thước + SHA-256 đã kiểm tra tay khi đưa vào ứng dụng.
- Whisper large-v3-turbo (bản CTranslate2, MIT) — nhận dạng giọng nói, đa ngôn ngữ.
- Giọng Piper tiếng Việt: vais1000 (dữ liệu CC BY 4.0) và vivos (dữ liệu CC BY-NC-SA 4.0 → chỉ dùng phi thương mại).
  Không đưa giọng 25hours_single vào vì không rõ giấy phép dữ liệu.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pas.core.errors import DependencyMissing
from pas.modules.safety.models import ModelFile, ModelPack, is_installed, model_path

WHISPER_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
_WHISPER_BASE = f"https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/resolve/{WHISPER_REVISION}/"
_WHISPER_DIR = "whisper-large-v3-turbo"
_WHISPER_LICENSE = "MIT (OpenAI Whisper, github.com/openai/whisper)"
_WHISPER_FILES = [
    ("model.bin", 1_617_884_929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
    ("config.json", 2_263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
    ("preprocessor_config.json", 340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
    ("tokenizer.json", 2_710_337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
    ("vocabulary.json", 1_068_114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
]
WHISPER = ModelPack(
    key="whisper-large-v3-turbo",
    title="Whisper large-v3-turbo (CTranslate2) — nhận dạng giọng nói, lấy transcript",
    files=tuple(ModelFile(f"{_WHISPER_DIR}/{n}", n, f"{_WHISPER_DIR}/{n}", _WHISPER_BASE + n, h, size,
                          _WHISPER_LICENSE, "") for n, size, h in _WHISPER_FILES),
    license=_WHISPER_LICENSE,
    source=f"huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo (chuyển đổi từ openai/whisper-large-v3-turbo), "
           f"commit {WHISPER_REVISION[:7]}",
)

PIPER_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
_PIPER_BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_REVISION}/vi/vi_VN/"


@dataclass(frozen=True)
class Voice:
    key: str
    label: str
    pack: ModelPack
    onnx: str  # đường dẫn tương đối trong thư mục models
    speakers: int
    sample_rate: int


def _voice(name: str, quality: str, onnx_size: int, onnx_sha: str, json_size: int, json_sha: str,
           license: str, source: str, note: str, label: str, speakers: int, rate: int) -> Voice:
    stem = f"vi_VN-{name}-{quality}"
    rel = f"piper/{stem}"
    files = (
        ModelFile(f"{stem}.onnx", stem, f"{rel}.onnx", f"{_PIPER_BASE}{name}/{quality}/{stem}.onnx", onnx_sha,
                  onnx_size, license, source),
        ModelFile(f"{stem}.json", stem, f"{rel}.onnx.json", f"{_PIPER_BASE}{name}/{quality}/{stem}.onnx.json",
                  json_sha, json_size, license, source),
    )
    pack = ModelPack(f"piper-{stem}", f"Giọng đọc Piper {stem} — {label}", files, license,
                     f"huggingface.co/rhasspy/piper-voices, commit {PIPER_REVISION[:7]}; {source}", note)
    return Voice(name, label, pack, f"{rel}.onnx", speakers, rate)


VOICES: dict[str, Voice] = {v.key: v for v in (
    _voice("vais1000", "medium", 63_201_294, "ec7c89e2c85f4d1edc24b6120c18aaf1bda614f06b511567eb9c7c0de15e2dab",
           4_860, "fafb9da1354ed4b77c31af228ed41fb41cd825c14cffa105454b25e6ae751ee0",
           "Mô hình Piper: MIT; dữ liệu huấn luyện VAIS-1000: CC BY 4.0 (cần ghi nguồn)",
           "dữ liệu VAIS-1000 (ieee-dataport.org)", "Khi đăng nội dung dùng giọng này, ghi nguồn: "
           "\"Giọng đọc: Piper vi_VN-vais1000, dữ liệu VAIS-1000 (CC BY 4.0)\".",
           "giọng nữ miền Bắc, rõ, 22 kHz (khuyên dùng)", 1, 22050),
    _voice("vivos", "x_low", 27_789_413, "6ab13374eb0862021a545befe7727aef59e16117f1c075aa9e0362237ecc98ae",
           5_592, "9dc373d69b0e4f39d864cb8ac9fe42cee49817a0f02d831e57a9ce6e944e234a",
           "Mô hình Piper: MIT; dữ liệu huấn luyện VIVOS: CC BY-NC-SA 4.0 (CHỈ dùng phi thương mại)",
           "dữ liệu VIVOS (ailab.hcmus.edu.vn)", "Dữ liệu VIVOS chỉ cho phép dùng PHI THƯƠNG MẠI — không dùng cho "
           "video kiếm tiền / quảng cáo. Ghi nguồn: \"Giọng đọc: Piper vi_VN-vivos, dữ liệu VIVOS (CC BY-NC-SA 4.0)\".",
           "65 người đọc (nam/nữ), 16 kHz — chỉ phi thương mại; ĐỌC SAI thanh huyền/nặng (mô hình thiếu ký hiệu thanh điệu)", 65, 16000),
)}

PACKS: dict[str, ModelPack] = {WHISPER.key: WHISPER, **{v.pack.key: v.pack for v in VOICES.values()}}


def whisper_dir(models_dir: Path) -> Path:
    return model_path(models_dir, WHISPER.files[0]).parent


def require_whisper(models_dir: Path) -> Path:
    if not is_installed(models_dir, WHISPER):
        raise DependencyMissing(
            f"Chưa tải mô hình nhận dạng giọng nói Whisper large-v3-turbo ({WHISPER.size / 1e9:.1f} GB, {WHISPER.license}).",
            "Ở trang 'Làm lại video', bấm 'Tải mô hình Whisper…' (cần mạng một lần).",
        )
    return whisper_dir(models_dir)


def require_voice(models_dir: Path, key: str) -> Path:
    v = VOICES[key]
    if not is_installed(models_dir, v.pack):
        raise DependencyMissing(
            f"Chưa tải giọng đọc {key} ({v.pack.size / 1e6:.0f} MB, {v.pack.license}).",
            "Ở trang 'Làm lại video' → bước 2, bấm 'Tải giọng đọc…' (cần mạng một lần).",
        )
    return Path(models_dir) / v.onnx
