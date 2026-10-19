"""Dự án: tên + thư mục đầu vào/đầu ra. Xóa dự án chỉ xóa bản ghi, không đụng tới file trên đĩa."""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass

from pas.core.db import Database, utcnow
from pas.core.errors import InvalidInput

MAX_NAME_LEN = 120


@dataclass
class Project:
    id: int
    name: str
    input_dir: str
    output_dir: str
    notes: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        return cls(**{k: row[k] for k in row.keys()})


def _clean_name(name: str) -> str:
    # NFC: cùng một chữ tiếng Việt có thể đến ở dạng tổ hợp (NFD) khi dán từ nguồn khác.
    name = " ".join(unicodedata.normalize("NFC", name or "").split())
    if not name:
        raise InvalidInput("Tên dự án không được để trống.", "Nhập một tên dễ nhớ, ví dụ: 'Phim ngắn tháng 10'.")
    if len(name) > MAX_NAME_LEN:
        raise InvalidInput(f"Tên dự án dài quá {MAX_NAME_LEN} ký tự.", "Rút gọn tên dự án.")
    return name


class ProjectRepository:
    def __init__(self, db: Database):
        self.db = db

    def _ensure_unique(self, name: str, exclude_id: int | None = None) -> None:
        # COLLATE NOCASE của SQLite chỉ gộp hoa/thường ASCII ("DỰ ÁN" ≠ "dự án"), nên so sánh casefold ở đây.
        key = name.casefold()
        for row in self.db.query("SELECT id, name FROM projects"):
            if row["id"] != exclude_id and row["name"].casefold() == key:
                raise InvalidInput(f"Đã có dự án tên '{row['name']}'.", "Chọn tên khác hoặc mở dự án đã có.")

    def create(self, name: str, input_dir: str = "", output_dir: str = "", notes: str = "") -> Project:
        name = _clean_name(name)
        self._ensure_unique(name)
        now = utcnow()
        try:
            cur = self.db.execute(
                "INSERT INTO projects (name, input_dir, output_dir, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, input_dir, output_dir, notes, now, now),
            )
        except sqlite3.IntegrityError:
            raise InvalidInput(f"Đã có dự án tên '{name}'.", "Chọn tên khác hoặc mở dự án đã có.") from None
        return self.get(cur.lastrowid)

    def get(self, project_id: int) -> Project | None:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return Project.from_row(row) if row else None

    def list(self) -> list[Project]:
        return [Project.from_row(r) for r in self.db.query("SELECT * FROM projects ORDER BY updated_at DESC, id DESC")]

    def update(self, project_id: int, *, name: str | None = None, input_dir: str | None = None,
               output_dir: str | None = None, notes: str | None = None) -> Project:
        current = self.get(project_id)
        if current is None:
            raise InvalidInput("Dự án không còn tồn tại.", "Tải lại danh sách dự án.")
        new_name = _clean_name(name) if name is not None else current.name
        self._ensure_unique(new_name, exclude_id=project_id)
        try:
            self.db.execute(
                "UPDATE projects SET name = ?, input_dir = ?, output_dir = ?, notes = ?, updated_at = ? WHERE id = ?",
                (
                    new_name,
                    current.input_dir if input_dir is None else input_dir,
                    current.output_dir if output_dir is None else output_dir,
                    current.notes if notes is None else notes,
                    utcnow(),
                    project_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise InvalidInput(f"Đã có dự án tên '{new_name}'.", "Chọn tên khác.") from None
        return self.get(project_id)

    def rename(self, project_id: int, new_name: str) -> Project:
        return self.update(project_id, name=new_name)

    def delete(self, project_id: int) -> None:
        self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
