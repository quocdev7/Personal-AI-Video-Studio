"""Mô hình của "Phim ngắn AI" — tải qua tác vụ dùng chung `model.download`, chỉ khi người dùng bấm tải và đồng ý.

URL cố định theo commit; kích thước + SHA-256 đã kiểm tra tay khi đưa vào ứng dụng.
- LCM Dreamshaper v7 (bản OpenVINO INT8 của Intel, MIT; mô hình gốc SimianLuo/LCM_Dreamshaper_v7) — tạo ảnh 4 bước
  trên CPU bằng OpenVINO GenAI. Không tải `safety_checker` (1,2 GB, OpenVINO GenAI không dùng) và `vae_encoder`
  (chỉ cần cho ảnh → ảnh).
- Giọng Piper tiếng Anh: libritts_r (904 người đọc, dữ liệu LibriTTS-R CC BY 4.0 — cần ghi nguồn), john, norman
  (nam) và kristin (nữ) — dữ liệu LibriVox public domain. Không đưa hfc_*/ryan (dữ liệu CC BY-NC-SA, chỉ phi
  thương mại) và alan (không rõ giấy phép dữ liệu).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from pas.core.errors import DependencyMissing
from pas.modules.remake.models import Voice, piper_voice
from pas.modules.safety.models import ModelFile, ModelPack, is_installed

INSTALL_HINT = "Cài thư viện tạo ảnh (OpenVINO GenAI, Apache-2.0, ~90 MB): `uv sync --extra aifilm`"

LCM_REVISION = "340a03e5488f36f7f222e6efa29819850ad7e4be"
_LCM_BASE = f"https://huggingface.co/OpenVINO/LCM_Dreamshaper_v7-int8-ov/resolve/{LCM_REVISION}/"
LCM_DIR = "lcm-dreamshaper-v7-int8-ov"
_LCM_LICENSE = "MIT (OpenVINO/LCM_Dreamshaper_v7-int8-ov, gốc SimianLuo/LCM_Dreamshaper_v7)"
_LCM_FILES = [
    ("feature_extractor/preprocessor_config.json", 466, "205d45875859846dc1ca211b9a19f5db72f47e953b396954ee34d48d8e95c3c6"),
    ("model_index.json", 679, "2b3ec5f46aec6f7540dbeaf18570dd3dcab5c834d124ee5f6648d38b37999180"),
    ("scheduler/scheduler_config.json", 562, "d1206b14caa024787013b1c0ff4a34b19218d659309540b8d79791ed981b3a1b"),
    ("text_encoder/config.json", 792, "9c9e2f460bb57cd72c1c5fafe5208717c58724e69479687c13a5149b1e26b795"),
    ("text_encoder/openvino_model.bin", 123_822_579, "d956758485dfbf416f58369beb4ee4524339fd299abd827993f77858d153d577"),
    ("text_encoder/openvino_model.xml", 612_576, "821fe5962a8fdb5a034bb90226a73389a831ae9f96cf9a47ddff9073bb651467"),
    ("tokenizer/merges.txt", 524_619, "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a"),
    ("tokenizer/openvino_detokenizer.bin", 616_799, "939b592bdf37c0ce490442b93454f8c4c135fd879f67dce7736acaaf963771b6"),
    ("tokenizer/openvino_detokenizer.xml", 12_725, "8d572a72765e4c99380a3843dde08a634001c81e3b5b0296d7a3cf4e1bb771dd"),
    ("tokenizer/openvino_tokenizer.bin", 1_425_650, "653d6f2b0bbd68ae2f888cf7fb68585f860dbd6cd28b74abd375daa6a255adfa"),
    ("tokenizer/openvino_tokenizer.xml", 29_497, "40b13724ef3cf0b7a4f906bb827b9df69b5106b1773b6509ed7d0d3db94f3f14"),
    ("tokenizer/special_tokens_map.json", 585, "a46982c1c09487f4ba4118d4d491a01d7e1b3a9e457662aa93f7c7159f11eaf7"),
    ("tokenizer/tokenizer_config.json", 769, "17157143496611cd1c355a158e892bb42816c2f6c065cc703fb463de9b56a54a"),
    ("tokenizer/vocab.json", 1_059_962, "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349"),
    ("unet/config.json", 1_925, "44d35f84e0843975dff7e86ae69983e4978e07b54193ac73c7de4b90b9d3372c"),
    ("unet/openvino_model.bin", 861_891_688, "5502e0cd53ac86125bbce5fd950592342f9e12ad02c429c137bd2c211dc231cf"),
    ("unet/openvino_model.xml", 2_351_105, "abf530a9ddfb966413508ca541b1b2ee3f646e66e0c85e1e03468e54b61f7ce5"),
    ("vae_decoder/config.json", 911, "be253b2a06624e1e7a1af19f99f15535f48ff1d72d56abfa4a7df07378bc6e06"),
    ("vae_decoder/openvino_model.bin", 49_647_853, "fd96c8fece425f22239b8c328ed3e3d580b506352d6f8b3192a17b9a85f03f7b"),
    ("vae_decoder/openvino_model.xml", 323_984, "e2ecdeba4a9ead4d63386108bee9e866b2cffaaacbf0ddefeb3a7e075c0bc6be"),
]
LCM = ModelPack(
    key="lcm-dreamshaper-v7-int8",
    title="LCM Dreamshaper v7 (OpenVINO INT8) — tạo ảnh minh họa trên CPU",
    files=tuple(ModelFile(f"{LCM_DIR}/{n}", Path(n).name, f"{LCM_DIR}/{n}", _LCM_BASE + n, h, size, _LCM_LICENSE, "")
                for n, size, h in _LCM_FILES),
    license=_LCM_LICENSE,
    source=f"huggingface.co/OpenVINO/LCM_Dreamshaper_v7-int8-ov commit {LCM_REVISION[:7]}",
    note="Ảnh do AI tạo có thể sai tay/mặt hoặc giống tác phẩm có sẵn — xem lại từng cảnh trước khi đăng. "
         "Không dùng để tạo ảnh người thật, nội dung lừa đảo hoặc vi phạm pháp luật.",
)

_PD = "Mô hình Piper: MIT; dữ liệu huấn luyện LibriVox: public domain"
_PD_NOTE = "Dữ liệu public domain — dùng thương mại được, không bắt buộc ghi nguồn."
EN_VOICES: dict[str, Voice] = {v.key: v for v in (
    piper_voice("libritts_r", "medium", 78_580_914, "10bb85e071d616fcf4071f369f1799d0491492ab3c5d552ec19fb548fac13195",
                20_123, "b471dc60d2d8335e819c393d196d6fbf792817f40051257b269878505bc9afb3",
                "Mô hình Piper: MIT; dữ liệu huấn luyện LibriTTS-R: CC BY 4.0 (cần ghi nguồn)",
                "dữ liệu LibriTTS-R (openslr.org/141)", "Khi đăng video dùng giọng này, ghi nguồn: "
                "\"Giọng đọc: Piper en_US-libritts_r, dữ liệu LibriTTS-R (CC BY 4.0)\".",
                "904 người đọc Anh-Mỹ (nam/nữ) — mỗi nhân vật một người đọc", 904, 22050, "en_US"),
    piper_voice("john", "medium", 63_531_379, "789c6c875726e627ddee93d51d8727859abe9c091c3d141591f4b83c2072e988",
                4_965, "af60f177b6b550f3d7a302720c0fb89e7f94a82b5dca464775ef63b1c69ba09a",
                _PD, "dữ liệu LibriVox (librivox.org)", _PD_NOTE, "nam Anh-Mỹ, trầm", 1, 22050, "en_US"),
    piper_voice("norman", "medium", 63_531_379, "b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71",
                4_968, "6c2db7f558a4a8deb9fe822583c1c5105f6c4e834dd0f9de8ad17a888ee9fe1d",
                _PD, "dữ liệu LibriVox (librivox.org)", _PD_NOTE, "nam Anh-Mỹ, kể chuyện", 1, 22050, "en_US"),
    piper_voice("kristin", "medium", 63_531_379, "5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4",
                4_968, "5681426d4aead22195de70531eeeeddb46493cfaffc5764b2ea3db73428b651c",
                _PD, "dữ liệu LibriVox (librivox.org)", _PD_NOTE, "nữ Anh-Mỹ", 1, 22050, "en_US"),
)}

PACKS: dict[str, ModelPack] = {LCM.key: LCM, **{v.pack.key: v.pack for v in EN_VOICES.values()}}


def require_runtime() -> None:
    if importlib.util.find_spec("openvino_genai") is None:
        raise DependencyMissing("Chưa cài thư viện tạo ảnh OpenVINO GenAI.", INSTALL_HINT)


def require_lcm(models_dir: Path) -> Path:
    if not is_installed(models_dir, LCM):
        raise DependencyMissing(
            f"Chưa tải mô hình tạo ảnh LCM Dreamshaper v7 ({LCM.size / 1e9:.2f} GB, {LCM.license}).",
            "Ở trang 'Phim ngắn AI', bấm 'Tải mô hình…' (cần mạng một lần).",
        )
    return Path(models_dir) / LCM_DIR


def require_en_voice(models_dir: Path, key: str) -> Path:
    v = EN_VOICES[key]
    if not is_installed(models_dir, v.pack):
        raise DependencyMissing(
            f"Chưa tải giọng đọc tiếng Anh {key} ({v.pack.size / 1e6:.0f} MB, {v.pack.license}).",
            "Ở trang 'Phim ngắn AI', bấm 'Tải mô hình…' (cần mạng một lần).",
        )
    return Path(models_dir) / v.onnx
