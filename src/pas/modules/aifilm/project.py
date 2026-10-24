"""Thư mục dự án "<tiêu đề> - phim AI": kịch bản, ảnh từng cảnh, giọng từng câu, video và ghi nguồn.

Mọi file ghi nguyên tử (file tạm → đổi tên). Ảnh lưu theo mã nội dung trong 'ảnh/kho' (sửa kịch bản rồi đổi thứ tự cảnh
không phải vẽ lại); 'ảnh/cảnh NN.png' là bản sao để người dùng xem nhanh.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pas.core.errors import InvalidInput
from pas.core.paths import unique_output_dir

MANIFEST = "aifilm.json"
VERSION = 1
SCRIPT_TXT = "kịch bản.txt"
IMAGES = "ảnh"
POOL = "ảnh/kho"
VOICES = "giọng"
VIDEO_MP4 = "phim.mp4"
CREDITS_TXT = "ghi nguồn.txt"


def shot_image_name(index: int) -> str:
    return f"{IMAGES}/cảnh {index:02d}.png"


def folder_name(title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip(" .")[:60] or "Phim AI"
    return f"{safe} - phim AI"


def new_project_dir(title: str, base: Path) -> Path:
    return unique_output_dir(Path(base) / folder_name(title))


def manifest_path(project_dir: Path) -> Path:
    return Path(project_dir) / MANIFEST


def load(project_dir: Path) -> dict:
    path = manifest_path(project_dir)
    if not path.is_file():
        raise InvalidInput(f"'{project_dir}' không phải thư mục dự án phim AI (thiếu {MANIFEST}).",
                           "Chạy bước 1 'Viết kịch bản' trước, hoặc chọn đúng thư mục '… - phim AI'.")
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


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_script(project_dir: Path) -> str:
    path = Path(project_dir) / SCRIPT_TXT
    if not path.is_file():
        raise InvalidInput(f"Dự án chưa có {SCRIPT_TXT}.", "Chạy bước 1 'Viết kịch bản' trước.")
    return path.read_text(encoding="utf-8")


def validate_project_dir(params: dict) -> Path:
    raw = params.get("project_dir")
    if not raw:
        raise InvalidInput("Chưa có dự án.", "Chạy bước 1 trước, hoặc bấm 'Mở dự án…' để chọn thư mục '… - phim AI'.")
    path = Path(raw)
    if not manifest_path(path).is_file():
        raise InvalidInput(f"'{path}' không phải thư mục dự án phim AI (thiếu {MANIFEST}).",
                           "Chọn đúng thư mục '… - phim AI' do bước 1 tạo ra.")
    return path
