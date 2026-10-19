from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from pas.core.deps import check_environment
from pas.jobs.models import JobStatus
from pas.services import AppServices
from pas.ui.widgets import PageHeader, button


class DashboardPage(QWidget):
    go_to_queue = Signal()

    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader("Tổng quan", "Tình trạng môi trường, tác vụ và các thông báo cần chú ý."))

        self.notice = QLabel()
        self.notice.setObjectName("errorBox")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.btn_queue = button("Mở hàng đợi")
        self.btn_queue.clicked.connect(self.go_to_queue)
        self.btn_queue.hide()
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.notice, 1)
        notice_row.addWidget(self.btn_queue)
        lay.addLayout(notice_row)

        jobs_box = QGroupBox("Tác vụ")
        jl = QHBoxLayout(jobs_box)
        self.counts = QLabel()
        jl.addWidget(self.counts)
        lay.addWidget(jobs_box)

        env_box = QGroupBox("Môi trường")
        el = QVBoxLayout(env_box)
        self.env_tree = QTreeWidget()
        self.env_tree.setHeaderLabels(["Thành phần", "Trạng thái", "Chi tiết", "Hướng xử lý"])
        self.env_tree.setRootIsDecorated(False)
        self.env_tree.setWordWrap(True)
        h = self.env_tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        el.addWidget(self.env_tree)
        recheck = button("Kiểm tra lại")
        recheck.clicked.connect(self.refresh_environment)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(recheck)
        el.addLayout(row)
        lay.addWidget(env_box, 1)

        self.refresh_environment()

    def refresh_environment(self) -> None:
        self.env_tree.clear()
        for st in check_environment(self.services.settings, self.services.paths):
            if st.ok:
                state = "✔ Sẵn sàng"
            else:
                state = "✖ Thiếu" if st.required else "○ Chưa cài (tùy chọn)"
            item = QTreeWidgetItem([st.name, state, st.detail, st.hint])
            item.setToolTip(2, st.detail)
            item.setToolTip(3, st.hint)
            self.env_tree.addTopLevelItem(item)

    def refresh(self) -> None:
        counts = self.services.job_repo.count_by_status()
        parts = [f"{s.label}: <b>{counts.get(s, 0)}</b>" for s in JobStatus]
        self.counts.setText(" · ".join(parts))
        interrupted = counts.get(JobStatus.INTERRUPTED, 0)
        if interrupted:
            self.notice.setText(
                f"Có {interrupted} tác vụ bị gián đoạn do ứng dụng đóng khi đang chạy. "
                "Mở Hàng đợi để chạy lại hoặc hủy."
            )
        self.notice.setVisible(bool(interrupted))
        self.btn_queue.setVisible(bool(interrupted))
