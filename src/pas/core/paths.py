"""Thư mục ứng dụng và quy tắc đặt tên file đầu ra an toàn (không bao giờ ghi đè)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pas.core.errors import InvalidInput

APP_DIR_NAME = "PersonalAIStudio"
DATA_DIR_ENV = "PAS_DATA_DIR"


def default_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def db_file(self) -> Path:
        return self.root / "app.db"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temp"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    def ensure(self) -> "AppPaths":
        for d in (self.root, self.logs_dir, self.temp_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def unique_output_path(path: Path) -> Path:
    """Trả về `path` nếu chưa tồn tại, ngược lại `ten (1).ext`, `ten (2).ext`..."""
    path = Path(path)
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())
    except OSError:
        return False


def ensure_not_source(output: Path, *sources: Path) -> None:
    """Chặn trường hợp file đầu ra trùng với một file nguồn."""
    for src in sources:
        if _same_file(Path(output), Path(src)):
            raise InvalidInput(
                f"File đầu ra trùng với file gốc: {src}",
                "Chọn thư mục đầu ra khác hoặc đổi tên file. Ứng dụng không ghi đè file gốc.",
            )
