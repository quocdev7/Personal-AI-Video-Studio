"""Mô hình của "Làm lại video" — tải qua tác vụ dùng chung `model.download`, chỉ khi người dùng bấm tải và đồng ý.

URL cố định theo commit; kích thước + SHA-256 đã kiểm tra tay khi đưa vào ứng dụng.
- Whisper large-v3-turbo (bản CTranslate2, MIT) — nhận dạng giọng nói, đa ngôn ngữ.
- Giọng Piper tiếng Việt: vais1000 (dữ liệu CC BY 4.0) và vivos (dữ liệu CC BY-NC-SA 4.0 → chỉ dùng phi thương mại).
  Không đưa giọng 25hours_single vào vì không rõ giấy phép dữ liệu.
- VieNeu-TTS v3 Turbo (Apache-2.0, cả mô hình, codec MOSS và giọng có sẵn — README ghi rõ dùng thương mại được):
  bản ONNX fp32 chạy CPU + bộ mã hóa người nói + khử ồn (để nhân bản giọng từ file mẫu).
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
_PIPER_ROOT = f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_REVISION}/"


@dataclass(frozen=True)
class Voice:
    key: str
    label: str
    pack: ModelPack
    onnx: str  # đường dẫn tương đối trong thư mục models
    speakers: int
    sample_rate: int


def piper_voice(name: str, quality: str, onnx_size: int, onnx_sha: str, json_size: int, json_sha: str,
                license: str, source: str, note: str, label: str, speakers: int, rate: int,
                locale: str = "vi_VN") -> Voice:
    """Giọng Piper trên huggingface.co/rhasspy/piper-voices (cố định commit PIPER_REVISION)."""
    stem = f"{locale}-{name}-{quality}"
    rel = f"piper/{stem}"
    base = f"{_PIPER_ROOT}{locale.split('_')[0]}/{locale}/{name}/{quality}/{stem}"
    files = (
        ModelFile(f"{stem}.onnx", stem, f"{rel}.onnx", f"{base}.onnx", onnx_sha, onnx_size, license, source),
        ModelFile(f"{stem}.json", stem, f"{rel}.onnx.json", f"{base}.onnx.json", json_sha, json_size, license, source),
    )
    pack = ModelPack(f"piper-{stem}", f"Giọng đọc Piper {stem} — {label}", files, license,
                     f"huggingface.co/rhasspy/piper-voices, commit {PIPER_REVISION[:7]}; {source}", note)
    return Voice(name, label, pack, f"{rel}.onnx", speakers, rate)


VOICES: dict[str, Voice] = {v.key: v for v in (
    piper_voice("vais1000", "medium", 63_201_294, "ec7c89e2c85f4d1edc24b6120c18aaf1bda614f06b511567eb9c7c0de15e2dab",
           4_860, "fafb9da1354ed4b77c31af228ed41fb41cd825c14cffa105454b25e6ae751ee0",
           "Mô hình Piper: MIT; dữ liệu huấn luyện VAIS-1000: CC BY 4.0 (cần ghi nguồn)",
           "dữ liệu VAIS-1000 (ieee-dataport.org)", "Khi đăng nội dung dùng giọng này, ghi nguồn: "
           "\"Giọng đọc: Piper vi_VN-vais1000, dữ liệu VAIS-1000 (CC BY 4.0)\".",
           "giọng nữ miền Bắc, rõ, 22 kHz (khuyên dùng)", 1, 22050),
    piper_voice("vivos", "x_low", 27_789_413, "6ab13374eb0862021a545befe7727aef59e16117f1c075aa9e0362237ecc98ae",
           5_592, "9dc373d69b0e4f39d864cb8ac9fe42cee49817a0f02d831e57a9ce6e944e234a",
           "Mô hình Piper: MIT; dữ liệu huấn luyện VIVOS: CC BY-NC-SA 4.0 (CHỈ dùng phi thương mại)",
           "dữ liệu VIVOS (ailab.hcmus.edu.vn)", "Dữ liệu VIVOS chỉ cho phép dùng PHI THƯƠNG MẠI — không dùng cho "
           "video kiếm tiền / quảng cáo. Ghi nguồn: \"Giọng đọc: Piper vi_VN-vivos, dữ liệu VIVOS (CC BY-NC-SA 4.0)\".",
           "65 người đọc (nam/nữ), 16 kHz — chỉ phi thương mại; ĐỌC SAI thanh huyền/nặng (mô hình thiếu ký hiệu thanh điệu)", 65, 16000),
)}

VIENEU_REVISION = "61b85e3d937fbbacb387714180e8182823512523"
CODEC_REVISION = "ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae"
_VIENEU_BASE = f"https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo/resolve/{VIENEU_REVISION}/"
_CODEC_BASE = f"https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX/resolve/{CODEC_REVISION}/"
VIENEU_DIR = "vieneu-v3-turbo"  # gốc: speaker_encoder.onnx, denoiser.onnx; onnx_update/: mô hình; codec/: MOSS
_VIENEU_LICENSE = "Apache-2.0 (VieNeu-TTS v3 Turbo, codec MOSS-Audio-Tokenizer-Nano và các giọng có sẵn)"
_VIENEU_FILES = [  # (đường dẫn trên nguồn, đường dẫn trong VIENEU_DIR, kích thước, SHA-256)
    (_VIENEU_BASE + "onnx_update/vieneu_backbone_shared.data", "onnx_update/vieneu_backbone_shared.data",
     415_319_040, "c7c072193db33d0542457e2612c7272c44c4279d1cafaf0aa4c379964911db2f"),
    (_VIENEU_BASE + "onnx_update/vieneu_v3_heads.npz", "onnx_update/vieneu_v3_heads.npz",
     52_219_622, "fb22484baa424bbb775133a6e5f0d00d6299b2b256fbe3312a864b85b9aed01e"),
    (_VIENEU_BASE + "onnx_update/vieneu_acoustic_cached.onnx", "onnx_update/vieneu_acoustic_cached.onnx",
     7_207_223, "f631e3387c788c3d8b9a5ac5df94952af5bc4c4d1049ff8a751e76a246fff2d4"),
    (_VIENEU_BASE + "onnx_update/vieneu_prefill.onnx", "onnx_update/vieneu_prefill.onnx",
     324_499, "27f8b064f6b57b5448e95d095f1959588c005d614678045c2b97ecccf3b7a0f7"),
    (_VIENEU_BASE + "onnx_update/vieneu_decode_step.onnx", "onnx_update/vieneu_decode_step.onnx",
     306_134, "bedc379cea61ea5d616312750d95ad3924e055856662d19187a889a5edc24ceb"),
    (_VIENEU_BASE + "onnx_update/config.json", "onnx_update/config.json",
     2_152, "17d89d414ee302a82db7b330bf57b4cdf8541569392119c81f552178cafcb79b"),
    (_VIENEU_BASE + "onnx_update/tokenizer.json", "onnx_update/tokenizer.json",
     22_320, "6cc6bcbe380b8c37bd9f2514e37c5dfa3e00e122c6e3125dae5c4afe48e39158"),
    (_VIENEU_BASE + "speaker_encoder.onnx", "speaker_encoder.onnx",
     28_303_423, "a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73"),
    (_VIENEU_BASE + "denoiser.onnx", "denoiser.onnx",
     42_661_414, "b7621953291cfe05e695a9c0ff4255aa2f93239fc17c26627e18b7b6b8f72f0b"),
    (_CODEC_BASE + "moss_audio_tokenizer_decode_full.onnx", "codec/moss_audio_tokenizer_decode_full.onnx",
     681_902, "0fbbafe3fd4afa2a019af5c5ced204af6e2d1db044fa40f021525d2aee95b4ac"),
    (_CODEC_BASE + "moss_audio_tokenizer_decode_shared.data", "codec/moss_audio_tokenizer_decode_shared.data",
     44_198_912, "e69d52e0f4e84ca27850557ee54face46632d3a5a16c89bd246c7c408466dcad"),
    (_CODEC_BASE + "moss_audio_tokenizer_decode_step.onnx", "codec/moss_audio_tokenizer_decode_step.onnx",
     351_400, "9527c86a29e1837edec1f74db57d5eeaadb3a715af3382703566460afed25855"),
    (_CODEC_BASE + "codec_browser_onnx_meta.json", "codec/codec_browser_onnx_meta.json",
     17_036, "3e291c883bb7d11ff2fe8e964e3e495519760358859f35c951254c7741592731"),
    (_CODEC_BASE + "moss_audio_tokenizer_encode.onnx", "codec/moss_audio_tokenizer_encode.onnx",
     815_775, "eadea4a645abdcf98714c7aead122ee2ce7da6e080f9f80b977cd1ca8e19473a"),
    (_CODEC_BASE + "moss_audio_tokenizer_encode.data", "codec/moss_audio_tokenizer_encode.data",
     44_507_136, "aa751265b2bab2887eac224484546b194875aa7494b607115439b3dc6b228a2c"),
]
VIENEU = ModelPack(
    key="vieneu-v3-turbo",
    title="VieNeu-TTS v3 Turbo (ONNX, CPU) — giọng đọc tiếng Việt 48 kHz + nhân bản giọng từ file mẫu",
    files=tuple(ModelFile(f"{VIENEU_DIR}/{rel}", Path(rel).name, f"{VIENEU_DIR}/{rel}", url, sha, size,
                          _VIENEU_LICENSE, "") for url, rel, size, sha in _VIENEU_FILES),
    license=_VIENEU_LICENSE,
    source=f"huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo commit {VIENEU_REVISION[:7]}; "
           f"huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX commit {CODEC_REVISION[:7]}",
    note="Giọng có sẵn được tác giả xác nhận dùng thương mại được. Nhân bản giọng từ file mẫu: bạn phải có quyền "
         "dùng giọng trong file mẫu (không nhân bản giọng người thật khi chưa được phép, không giả mạo).",
)

PACKS: dict[str, ModelPack] = {WHISPER.key: WHISPER, VIENEU.key: VIENEU,
                               **{v.pack.key: v.pack for v in VOICES.values()}}


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


def require_vieneu(models_dir: Path) -> Path:
    """Thư mục gốc của VieNeu (đủ file đúng kích thước)."""
    if not is_installed(models_dir, VIENEU):
        raise DependencyMissing(
            f"Chưa tải mô hình giọng đọc VieNeu-TTS v3 Turbo ({VIENEU.size / 1e6:.0f} MB, {VIENEU.license}).",
            "Ở trang 'Làm lại video' → bước 2, chọn giọng VieNeu rồi bấm 'Tải giọng đọc…' (cần mạng một lần).",
        )
    return Path(models_dir) / VIENEU_DIR
