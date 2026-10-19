"""Cấu hình người dùng, lưu JSON. File hỏng không làm ứng dụng không khởi động được."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # Để trống = tự tìm (PATH, winget, tools\ffmpeg\bin).
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    default_output_dir: str = ""
    # Máy chỉ có CPU: chạy song song nhiều tác vụ AI làm tất cả chậm đi.
    max_concurrent_jobs: int = 1
    min_free_disk_gb: float = 2.0
    keep_temp_files: bool = False

    def validated(self) -> "Settings":
        if self.max_concurrent_jobs < 1:
            self.max_concurrent_jobs = 1
        if self.max_concurrent_jobs > 4:
            self.max_concurrent_jobs = 4
        if self.min_free_disk_gb < 0:
            self.min_free_disk_gb = 0.0
        return self


def _coerce(name: str, value, default):
    """Giữ giá trị nếu đúng kiểu (cho phép int → float), ngược lại dùng mặc định."""
    expected = type(default)
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, expected) and not (expected is int and isinstance(value, bool)):
        return value
    log.warning("Cấu hình '%s' sai kiểu (%r), dùng mặc định %r", name, value, default)
    return default


def load_settings(path: Path) -> Settings:
    path = Path(path)
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("nội dung không phải object JSON")
    except (OSError, ValueError) as exc:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        log.error("File cấu hình hỏng (%s). Sao lưu thành %s và dùng mặc định.", exc, backup)
        try:
            os.replace(path, backup)
        except OSError:
            log.exception("Không sao lưu được file cấu hình hỏng")
        return Settings()

    defaults = Settings()
    values = {}
    for f in fields(Settings):
        default = getattr(defaults, f.name)
        values[f.name] = _coerce(f.name, raw[f.name], default) if f.name in raw else default
    return Settings(**values).validated()


def save_settings(settings: Settings, path: Path) -> None:
    """Ghi nguyên tử: ghi file tạm rồi thay thế, tránh file dở dang khi mất điện."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
