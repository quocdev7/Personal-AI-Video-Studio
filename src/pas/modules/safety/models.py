"""Tải mô hình AI (chỉ khi người dùng bấm tải và xác nhận). Kiểm duyệt: NudeNet đi kèm gói pip; CLIP phải tải về.
Module khác (Làm lại video: Whisper, giọng Piper) dùng chung bộ tải qua `ModelPack` — một mô hình gồm nhiều file.

Tải: URL cố định theo commit (không tự đổi phiên bản), ghi `*.part` rồi kiểm tra kích thước + SHA-256 mới đổi tên.
Chỉ đọc dữ liệu (trọng số ONNX), không chạy file thực thi nào.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pas.core.disk import GB, ensure_free_space
from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput
from pas.jobs.context import JobContext
from pas.jobs.registry import TaskRegistry, TaskSpec

log = logging.getLogger(__name__)

DOWNLOAD_KIND = "model.download"
INSTALL_HINT = "Cài thư viện kiểm duyệt (NudeNet MIT + onnxruntime MIT, ~12 MB mô hình đi kèm): `uv sync --extra safety`"
CHUNK = 1 << 20
TIMEOUT = 30


@dataclass(frozen=True)
class ModelFile:
    key: str
    title: str
    filename: str
    url: str
    sha256: str
    size: int
    license: str
    source: str


CLIP_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
CLIP_VISION = ModelFile(
    key="clip-vit-b32-vision",
    title="CLIP ViT-B/32 — phần hình ảnh, lượng tử hóa (nhận diện bạo lực / máu me)",
    filename="clip-vit-b32-vision-quantized.onnx",
    url=f"https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{CLIP_REVISION}/onnx/vision_model_quantized.onnx",
    sha256="583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    size=89_117_001,
    license="MIT (OpenAI CLIP, github.com/openai/CLIP)",
    source=f"huggingface.co/Xenova/clip-vit-base-patch32 (bản ONNX của openai/clip-vit-base-patch32), commit {CLIP_REVISION[:7]}",
)


@dataclass(frozen=True)
class ModelPack:
    """Mô hình gồm nhiều file (vd thư mục CTranslate2 của Whisper). `filename` của từng file có thể chứa thư mục con."""
    key: str
    title: str
    files: tuple[ModelFile, ...]
    license: str
    source: str
    note: str = ""  # điều kiện dùng cần người dùng biết trước khi tải (vd chỉ dùng phi thương mại)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


Model = ModelFile | ModelPack
MODELS: dict[str, Model] = {CLIP_VISION.key: CLIP_VISION}


def files_of(model: Model) -> tuple[ModelFile, ...]:
    return model.files if isinstance(model, ModelPack) else (model,)


def describe(model: Model) -> str:
    """Nội dung hộp xác nhận trước khi tải: dung lượng, giấy phép, nguồn (bắt buộc hiện trước mọi lần tải)."""
    text = (f"{model.title}\n\nDung lượng: {model.size / 1e6:,.0f} MB\nGiấy phép: {model.license}\n"
            f"Nguồn: {model.source}")
    note = getattr(model, "note", "")
    return text + (f"\n\nLưu ý: {note}" if note else "")


def model_path(models_dir: Path, model: ModelFile) -> Path:
    return Path(models_dir) / model.filename


def is_installed(models_dir: Path, model: Model) -> bool:
    """Kiểm tra nhanh theo kích thước (SHA-256 đã được kiểm khi tải)."""
    for f in files_of(model):
        try:
            if model_path(models_dir, f).stat().st_size != f.size:
                return False
        except OSError:
            return False
    return True


def require_runtime() -> None:
    if importlib.util.find_spec("onnxruntime") is None or importlib.util.find_spec("PIL") is None:
        raise DependencyMissing("Chưa cài thư viện chạy mô hình kiểm duyệt (onnxruntime, Pillow).", INSTALL_HINT)


def nudenet_model() -> Path:
    """File 320n.onnx đi kèm gói nudenet (không import nudenet: gói đó cần cv2 lúc import)."""
    spec = importlib.util.find_spec("nudenet")
    if spec is None or not spec.submodule_search_locations:
        raise DependencyMissing("Chưa cài NudeNet (nhận diện khỏa thân).", INSTALL_HINT)
    path = Path(next(iter(spec.submodule_search_locations))) / "320n.onnx"
    if not path.is_file():
        raise DependencyMissing(f"Gói NudeNet thiếu file mô hình {path.name}.", "Cài lại: `uv sync --extra safety --reinstall-package nudenet`")
    return path


def require_model(models_dir: Path, model: ModelFile) -> Path:
    if not is_installed(models_dir, model):
        raise DependencyMissing(
            f"Chưa tải mô hình {model.title} ({model.size / 1e6:.0f} MB, giấy phép {model.license}).",
            "Ở trang 'Tạo clip ngắn', bấm 'Tải mô hình bạo lực…' (cần mạng một lần), hoặc bỏ chọn mục bạo lực.",
        )
    return model_path(models_dir, model)


def download(model: ModelFile, models_dir: Path, ctx: JobContext, done: int = 0, total: int | None = None) -> Path:
    """`done`/`total`: số byte đã tải của cả gói và tổng gói (để thanh tiến trình chạy liền mạch qua nhiều file)."""
    final = model_path(models_dir, model)
    total = total or model.size
    if is_installed(models_dir, model):
        ctx.log(f"Đã có {final.name}, không tải lại")
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    ctx.log(f"Tải {model.url} → {final}")
    req = urllib.request.Request(model.url, headers={"User-Agent": "PersonalAIStudio/0.1"})
    digest = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(part, "wb") as f:
            while True:
                ctx.checkpoint()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                if got + len(chunk) > model.size:
                    raise ExternalToolError(f"File tải về lớn hơn dự kiến ({model.size} byte) — đã dừng.",
                                            "Nguồn có thể đã thay đổi. Báo lại để cập nhật ứng dụng.")
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                ctx.report((done + got) / total, f"Đã tải {(done + got) / 1e6:.1f}/{total / 1e6:.1f} MB")
        if got != model.size:
            raise ExternalToolError(f"Tải chưa đủ: {got}/{model.size} byte.", "Kết nối bị ngắt. Bấm Chạy lại.")
        if digest.hexdigest() != model.sha256:
            raise ExternalToolError("File tải về không khớp mã SHA-256 — đã xóa, không dùng.",
                                    "File có thể bị hỏng khi tải hoặc nguồn đã thay đổi. Thử lại; nếu lặp lại, báo lỗi.")
        os.replace(part, final)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ExternalToolError(f"Không tải được mô hình: {reason}",
                                "Kiểm tra kết nối mạng (hoặc proxy) rồi bấm Chạy lại ở trang Tác vụ.") from exc
    finally:
        part.unlink(missing_ok=True)
    return final


def download_model(model: Model, models_dir: Path, ctx: JobContext) -> Path:
    """Tải mọi file của mô hình (file đã có đúng kích thước thì bỏ qua). Trả về file (ModelFile) hoặc thư mục gốc
    chung của gói (ModelPack)."""
    missing = sum(f.size for f in files_of(model) if not is_installed(models_dir, f))
    if missing:
        ensure_free_space(Path(models_dir), missing + GB // 2)
    done, total = 0, model.size
    paths = []
    for f in files_of(model):
        paths.append(download(f, models_dir, ctx, done, total))
        done += f.size
    if isinstance(model, ModelPack):
        return Path(os.path.commonpath([str(p.parent) for p in paths]))
    return paths[0]


def validate_download(params: dict, models: dict[str, Model] | None = None) -> None:
    if params.get("model") not in (models or MODELS):
        raise InvalidInput(f"Mô hình không xác định: {params.get('model')!r}")


def make_download_handler(models_dir: Path, models: dict[str, Model] | None = None):
    catalog = models or MODELS

    def run(ctx: JobContext) -> dict:
        model = catalog[ctx.params["model"]]
        ctx.report(0.0, f"Đang tải {model.title}")
        path = download_model(model, models_dir, ctx)
        ctx.report(1.0, "Đã tải và kiểm tra mô hình")
        return {"model": model.key, "path": str(path), "size_bytes": model.size, "license": model.license}

    return run


def register(registry: TaskRegistry, models_dir: Path, extra: dict[str, Model] | None = None) -> None:
    """`extra`: mô hình của module khác dùng chung tác vụ tải."""
    catalog = {**MODELS, **(extra or {})}
    registry.register(TaskSpec(DOWNLOAD_KIND, "Tải mô hình", make_download_handler(models_dir, catalog),
                               lambda params: validate_download(params, catalog)))
