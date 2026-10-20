"""Lọc từ thô tục / xúc phạm theo danh sách (người dùng sửa được).

- Mục có chữ Hán: so khớp chuỗi con (tiếng Trung không có dấu cách giữa các từ).
- Mục chữ La-tinh (Việt có dấu, Anh): so khớp nguyên từ, không phân biệt hoa thường; "lồn" và "lon" là hai mục khác nhau.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pas.modules.vietsub.ocr import has_cjk

DEFAULT_FILE = Path(__file__).with_name("profanity_default.txt")
USER_FILE_NAME = "profanity.txt"
MASK = "***"


def parse_words(text: str) -> list[str]:
    words = []
    for raw in text.splitlines():
        line = unicodedata.normalize("NFC", raw.split("#", 1)[0]).strip().lower()
        if line and line not in words:
            words.append(line)
    return words


class WordFilter:
    def __init__(self, words: list[str]):
        self.words = words
        cjk = sorted((w for w in words if has_cjk(w)), key=len, reverse=True)
        latin = sorted((w for w in words if not has_cjk(w)), key=len, reverse=True)
        parts = [re.escape(w) for w in cjk]
        parts += [rf"(?<!\w){re.escape(w)}(?!\w)" for w in latin]
        self._re = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    @classmethod
    def load(cls, path: Path) -> "WordFilter":
        return cls(parse_words(Path(path).read_text(encoding="utf-8-sig")))

    def find(self, text: str) -> list[str]:
        if self._re is None or not text:
            return []
        return [m.group(0) for m in self._re.finditer(unicodedata.normalize("NFC", text))]

    def mask(self, text: str) -> str:
        if self._re is None or not text:
            return text
        return self._re.sub(MASK, unicodedata.normalize("NFC", text))


def word_list_path(app_root: Path) -> Path:
    """Danh sách đang dùng: file của người dùng trong thư mục dữ liệu nếu có, ngược lại danh sách mặc định."""
    user = Path(app_root) / USER_FILE_NAME
    return user if user.is_file() else DEFAULT_FILE


def ensure_user_list(app_root: Path) -> Path:
    """Tạo bản sao danh sách mặc định để người dùng sửa (không ghi đè file đã có)."""
    user = Path(app_root) / USER_FILE_NAME
    if not user.exists():
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text(DEFAULT_FILE.read_text(encoding="utf-8"), encoding="utf-8-sig")
    return user
