"""Thư mục dự án "<tên video> - làm lại": mọi bước đọc/ghi chung file `remake.json` và các file đầu ra trong đó.

Video gốc chỉ được đọc; mọi file ghi ra đều nằm trong thư mục dự án và được ghi nguyên tử (file tạm → đổi tên).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pas.core.errors import InvalidInput
from pas.core.paths import unique_output_dir

MANIFEST = "remake.json"
VERSION = 1

TRANSCRIPT_SRT = "transcript gốc.srt"
TRANSLATION_SRT = "bản dịch.srt"
SCRIPT_TXT = "kịch bản.txt"
VOICE_WAV = "giọng đọc.wav"
VOICE_JSON = "voice.json"
VIDEO_MP4 = "video mới.mp4"
SEO_TXT = "SEO.txt"


def thumbnail_name(i: int) -> str:
    return f"thumbnail {i}.jpg"


def folder_name(src: Path) -> str:
    return f"{src.stem} - làm lại"


def new_project_dir(src: Path, base: Path) -> Path:
    return unique_output_dir(Path(base) / folder_name(src))


def manifest_path(project_dir: Path) -> Path:
    return Path(project_dir) / MANIFEST


def load(project_dir: Path) -> dict:
    path = manifest_path(project_dir)
    if not path.is_file():
        raise InvalidInput(f"'{project_dir}' không phải thư mục dự án làm lại video (thiếu {MANIFEST}).",
                           "Chạy bước 1 'Lấy SUB & viết lại' trước, hoặc chọn đúng thư mục '… - làm lại'.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("nội dung không phải object JSON")
    except (OSError, ValueError) as exc:
        raise InvalidInput(f"Không đọc được {path}: {exc}",
                           "File dự án bị hỏng hoặc bị sửa tay sai cú pháp. Chạy lại bước 1 để tạo dự án mới.") from exc
    return data


def save(project_dir: Path, data: dict) -> None:
    path = manifest_path(project_dir)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def source_of(data: dict) -> Path:
    src = Path(data.get("source", ""))
    if not src.is_file():
        raise InvalidInput(f"Không tìm thấy video gốc của dự án: {src}",
                           "Video gốc đã bị di chuyển/đổi tên. Đưa file về chỗ cũ hoặc chạy lại bước 1.")
    return src


def validate_project_dir(params: dict) -> Path:
    raw = params.get("project_dir")
    if not raw:
        raise InvalidInput("Chưa có dự án.", "Chạy bước 1 trước, hoặc bấm 'Mở dự án…' để chọn thư mục '… - làm lại'.")
    path = Path(raw)
    if not manifest_path(path).is_file():
        raise InvalidInput(f"'{path}' không phải thư mục dự án làm lại video (thiếu {MANIFEST}).",
                           "Chọn đúng thư mục '… - làm lại' do bước 1 tạo ra.")
    return path
