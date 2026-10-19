from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pas.core.errors import PasError
from pas.projects.repository import Project
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, hint_label, open_in_explorer, show_error


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget, project: Project | None = None):
        super().__init__(parent)
        self.setWindowTitle("Sửa dự án" if project else "Dự án mới")
        self.setMinimumWidth(560)
        self.name = QLineEdit(project.name if project else "")
        self.input_dir = PathPicker("dir", placeholder="Thư mục chứa video gốc (không bắt buộc)")
        self.output_dir = PathPicker("dir", placeholder="Nơi lưu kết quả (không bắt buộc)")
        if project:
            self.input_dir.set_path(project.input_dir)
            self.output_dir.set_path(project.output_dir)
        form = QFormLayout()
        form.addRow("Tên dự án", self.name)
        form.addRow("Thư mục đầu vào", self.input_dir)
        form.addRow("Thư mục đầu ra", self.output_dir)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Lưu")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint_label("Ứng dụng không bao giờ ghi đè file trong thư mục đầu vào. "
                                 "Nên chọn thư mục đầu ra khác thư mục đầu vào."))
        lay.addWidget(buttons)


class ProjectsPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Dự án",
            "Nhóm các video và kết quả theo dự án. Mỗi dự án có thư mục đầu vào và đầu ra riêng.",
        ))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tên", "Thư mục đầu vào", "Thư mục đầu ra", "Cập nhật"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.btn_new = button("Dự án mới", "primary")
        self.btn_edit = button("Sửa / đổi tên")
        self.btn_open_in = button("Mở thư mục đầu vào")
        self.btn_open_out = button("Mở thư mục đầu ra")
        self.btn_delete = button("Xóa khỏi danh sách", "danger")
        self.btn_new.clicked.connect(self._new)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_open_in.clicked.connect(lambda: self._open("input_dir"))
        self.btn_open_out.clicked.connect(lambda: self._open("output_dir"))
        self.btn_delete.clicked.connect(self._delete)
        row = QHBoxLayout()
        for b in (self.btn_new, self.btn_edit, self.btn_open_in, self.btn_open_out):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_delete)
        lay.addLayout(row)
        lay.addWidget(self.table, 1)
        self.refresh()

    def _selected(self) -> Project | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        pid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self.services.projects.get(pid)

    def refresh(self) -> None:
        projects = self.services.projects.list()
        self.table.setRowCount(len(projects))
        for r, p in enumerate(projects):
            for c, v in enumerate([p.name, p.input_dir, p.output_dir, p.updated_at.replace("T", " ")[:16]]):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                self.table.setItem(r, c, item)
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, p.id)
        self._update_buttons()

    def _update_buttons(self) -> None:
        p = self._selected()
        self.btn_edit.setEnabled(p is not None)
        self.btn_delete.setEnabled(p is not None)
        self.btn_open_in.setEnabled(bool(p and p.input_dir))
        self.btn_open_out.setEnabled(bool(p and p.output_dir))

    def _new(self) -> None:
        dlg = ProjectDialog(self)
        while dlg.exec():
            try:
                self.services.projects.create(dlg.name.text(), dlg.input_dir.path(), dlg.output_dir.path())
            except PasError as exc:
                show_error(self, exc)
                continue
            break
        self.refresh()

    def _edit(self) -> None:
        p = self._selected()
        if not p:
            return
        dlg = ProjectDialog(self, p)
        while dlg.exec():
            try:
                self.services.projects.update(p.id, name=dlg.name.text(), input_dir=dlg.input_dir.path(),
                                              output_dir=dlg.output_dir.path())
            except PasError as exc:
                show_error(self, exc)
                continue
            break
        self.refresh()

    def _open(self, attr: str) -> None:
        p = self._selected()
        if p and getattr(p, attr):
            open_in_explorer(self, getattr(p, attr))

    def _delete(self) -> None:
        p = self._selected()
        if not p:
            return
        answer = QMessageBox.question(
            self, "Xóa dự án",
            f"Xóa dự án '{p.name}' khỏi danh sách?\n\nCác file video và kết quả trên ổ đĩa KHÔNG bị xóa.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.services.projects.delete(p.id)
            self.refresh()
