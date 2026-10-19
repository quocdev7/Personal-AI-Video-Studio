"""Bảng tác vụ dùng chung cho trang Hàng đợi và Lịch sử."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from pas.jobs.models import ACTIVE_STATUSES, RETRYABLE_STATUSES, Job, JobStatus
from pas.services import AppServices
from pas.ui.widgets import button, format_duration, open_in_explorer

COLUMNS = ["#", "Tên", "Loại", "Trạng thái", "Tiến trình", "Thời gian", "Thông điệp"]


class JobsView(QWidget):
    def __init__(self, services: AppServices, statuses: frozenset[JobStatus], parent: QWidget | None = None):
        super().__init__(parent)
        self.services = services
        self.statuses = statuses
        self._jobs: dict[int, Job] = {}

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_detail)

        self.btn_pause = button("Tạm dừng")
        self.btn_resume = button("Tiếp tục")
        self.btn_cancel = button("Hủy", "danger")
        self.btn_retry = button("Chạy lại")
        self.btn_open = button("Mở thư mục kết quả")
        self.btn_pause.clicked.connect(lambda: self._act(self.services.jobs.pause))
        self.btn_resume.clicked.connect(lambda: self._act(self.services.jobs.resume))
        self.btn_cancel.clicked.connect(lambda: self._act(self.services.jobs.cancel))
        self.btn_retry.clicked.connect(lambda: self._act(self.services.jobs.retry))
        self.btn_open.clicked.connect(self._open_output)
        actions = QHBoxLayout()
        for b in (self.btn_pause, self.btn_resume, self.btn_cancel, self.btn_retry, self.btn_open):
            actions.addWidget(b)
        actions.addStretch(1)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorBox")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.hide()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Chọn một tác vụ để xem log.")

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addLayout(actions)
        dl.addWidget(self.error_label)
        dl.addWidget(self.log_view, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        self._update_detail()

    def selected_job(self) -> Job | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return self._jobs.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self) -> None:
        selected = self.selected_job()
        selected_id = selected.id if selected else None
        jobs = self.services.job_repo.list(self.statuses)
        self._jobs = {j.id: j for j in jobs}
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = [
                str(job.id), job.title, self.services.registry.display_name(job.kind), job.status.label,
                f"{job.progress * 100:.0f}%", format_duration(job.elapsed_seconds),
                job.error or job.message,
            ]
            for col, value in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, col, item)
                item.setText(value)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, job.id)
            if job.id == selected_id:
                self.table.selectRow(row)
        self._update_detail()

    def _act(self, fn) -> None:
        job = self.selected_job()
        if job:
            fn(job.id)
            self.refresh()

    def _open_output(self) -> None:
        job = self.selected_job()
        if job and job.result and job.result.get("output_dir"):
            open_in_explorer(self, job.result["output_dir"])

    def _update_detail(self) -> None:
        job = self.selected_job()
        status = job.status if job else None
        self.btn_pause.setEnabled(status == JobStatus.RUNNING)
        self.btn_resume.setEnabled(status == JobStatus.PAUSED)
        self.btn_cancel.setEnabled(status in ACTIVE_STATUSES)
        self.btn_retry.setEnabled(status in RETRYABLE_STATUSES)
        self.btn_open.setEnabled(bool(job and job.result and job.result.get("output_dir")
                                      and Path(job.result["output_dir"]).exists()))
        if job and job.error:
            self.error_label.setText(f"<b>{html.escape(job.error)}</b><br>{html.escape(job.error_hint or '')}")
            self.error_label.show()
        else:
            self.error_label.hide()
        if job:
            lines = [f"{e.ts}  {e.level:<5}  {e.message}" for e in self.services.job_repo.logs(job.id)]
            text = "\n".join(lines)
            if self.log_view.toPlainText() != text:
                self.log_view.setPlainText(text)
                self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        else:
            self.log_view.clear()
