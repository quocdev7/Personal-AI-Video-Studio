"""Tạo src/pas/modules/safety/clip_prompts.npy: vector văn bản CLIP của các câu mô tả trong safety/detect.py.

Chỉ chạy khi đổi danh sách câu (người dùng cuối không cần). Cần text_model_quantized.onnx + tokenizer.json cùng
commit với mô hình ảnh (xem safety/models.py), tải từ huggingface.co/Xenova/clip-vit-base-patch32:

    uv run --with tokenizers python scripts/build_clip_prompts.py <thư mục chứa 2 file trên>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from pas.modules.safety.detect import PROMPTS_FILE, SAFE_PROMPTS, UNSAFE_PROMPTS

EOS = 49407  # <|endoftext|>: CLIP lấy vector ở vị trí token này (token có id lớn nhất), đệm bằng chính nó


def main(folder: Path) -> None:
    tok = Tokenizer.from_file(str(folder / "tokenizer.json"))
    session = ort.InferenceSession(str(folder / "text_model_quantized.onnx"), providers=["CPUExecutionProvider"])
    enc = tok.encode_batch(UNSAFE_PROMPTS + SAFE_PROMPTS)
    length = max(len(e.ids) for e in enc)
    ids = np.array([e.ids + [EOS] * (length - len(e.ids)) for e in enc], dtype=np.int64)
    text = session.run(["text_embeds"], {"input_ids": ids})[0]
    text /= np.linalg.norm(text, axis=1, keepdims=True)
    np.save(PROMPTS_FILE, text.astype(np.float32))
    print(f"Đã ghi {PROMPTS_FILE} ({text.shape[0]} câu × {text.shape[1]})")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
