from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from pas.core.errors import PasError

log = logging.getLogger(__name__)


def show_error(parent: QWidget, exc: BaseException, title: str = "Có lỗi") -> None:
    if isinstance(exc, PasError):
        text = exc.message
        info = exc.hint or ""
    else:
        log.exception("Lỗi không mong đợi trong giao diện", exc_info=exc)
        text = f"Lỗi không mong đợi: {type(exc).__name__}: {exc}"
        info = "Đây là lỗi của ứng dụng. Chi tiết đã được ghi vào log (Cài đặt → Mở thư mục log)."
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    if info:
        box.setInformativeText(info)
    box.exec()


def open_in_explorer(parent: QWidget, path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        show_error(parent, PasError(f"Thư mục không tồn tại: {p}", "Thư mục có thể đã bị di chuyển hoặc xóa."))
        return
    os.startfile(str(p))  # noqa: S606 — chỉ mở thư mục/file người dùng đã chọn


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class PageHeader(QWidget):
    def __init__(self, title: str, purpose: str, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        p = QLabel(purpose)
        p.setObjectName("pagePurpose")
        p.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(p)


def hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


class PathPicker(QWidget):
    """Ô đường dẫn + nút Chọn; hỗ trợ kéo thả file/thư mục."""

    path_changed = Signal(str)

    def __init__(self, mode: str = "file", file_filter: str = "", placeholder: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        assert mode in ("file", "dir", "exe")
        self.mode = mode
        self.file_filter = file_filter
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder or ("Kéo thả file vào đây hoặc bấm Chọn" if mode != "dir" else "Chọn thư mục"))
        self.edit.textChanged.connect(self.path_changed)
        self.edit.setAcceptDrops(False)  # để PathPicker nhận drop thay vì chèn "file:///..." vào ô
        btn = QPushButton("Chọn…")
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        self.setAcceptDrops(True)

    def path(self) -> str:
        return self.edit.text().strip().strip('"')

    def set_path(self, value: str) -> None:
        self.edit.setText(value)

    def _browse(self) -> None:
        start = self.path() or str(Path.home())
        if self.mode == "dir":
            chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Chọn file", start, self.file_filter)
        if chosen:
            self.set_path(str(Path(chosen)))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.set_path(str(Path(urls[0].toLocalFile())))
            event.acceptProposedAction()


def button(text: str, kind: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b
