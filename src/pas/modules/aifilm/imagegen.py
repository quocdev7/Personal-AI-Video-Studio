"""Vẽ ảnh từng cảnh bằng LCM Dreamshaper v7 (OpenVINO, chạy trên CPU, không cần mạng).

Đo trên máy này: nạp mô hình ~8 s; ảnh 512×912 mất ~28 s với 6 bước (~22 s với 4 bước "nhanh").
GPU tích hợp chậm hơn CPU (nạp 47 s, 30–49 s/ảnh) nên luôn dùng CPU.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from pas.core.errors import DependencyMissing, ExternalToolError, JobCancelled
from pas.modules.aifilm.models import INSTALL_HINT

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 512, 912  # ~9:16, bội số của 8 (yêu cầu của UNet)
STEPS = 6
FAST_STEPS = 4
GUIDANCE = 8.5  # mặc định của LCM Dreamshaper v7
DEVICE = "CPU"


class Generator(Protocol):
    def generate(self, prompt: str, seed: int, steps: int, cancelled: Callable[[], bool]) -> "Image": ...


class OpenVinoGenerator:
    def __init__(self, model_dir: Path) -> None:
        try:
            import openvino_genai as ov_genai
        except ImportError as exc:
            raise DependencyMissing("Chưa cài thư viện vẽ ảnh (openvino-genai).", INSTALL_HINT) from exc
        try:
            self._pipe = ov_genai.Text2ImagePipeline(str(model_dir), DEVICE)
        except Exception as exc:  # OpenVINO báo lỗi bằng RuntimeError chung
            raise ExternalToolError(f"Không nạp được mô hình vẽ ảnh: {exc}",
                                    "File mô hình có thể hỏng. Xóa thư mục mô hình rồi bấm 'Tải mô hình…' lại.") from exc

    def generate(self, prompt: str, seed: int, steps: int, cancelled: Callable[[], bool]) -> "Image":
        from PIL import Image

        try:
            tensor = self._pipe.generate(prompt, width=WIDTH, height=HEIGHT, num_inference_steps=steps,
                                         guidance_scale=GUIDANCE, rng_seed=seed,
                                         callback=lambda step, total, latent: cancelled())
        except Exception as exc:
            if cancelled():
                raise JobCancelled() from exc
            raise ExternalToolError(f"Vẽ ảnh lỗi: {exc}", "Xem log; thử lại hoặc rút gọn mô tả cảnh.") from exc
        if cancelled():  # callback trả True → OpenVINO dừng sớm và trả ảnh dở
            raise JobCancelled()
        data = tensor.data[0]
        if data.shape != (HEIGHT, WIDTH, 3):
            raise ExternalToolError(f"Mô hình trả ảnh kích thước lạ {data.shape}.", "Tải lại mô hình vẽ ảnh.")
        return Image.fromarray(data)


def load(model_dir: Path) -> Generator:
    return OpenVinoGenerator(model_dir)
