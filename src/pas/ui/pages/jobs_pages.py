from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from pas.jobs.models import ACTIVE_STATUSES, FINISHED_STATUSES, JobStatus
from pas.services import AppServices
from pas.ui.jobs_view import JobsView
from pas.ui.widgets import PageHeader, hint_label


class QueuePage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Hàng đợi tác vụ",
            "Các tác vụ đang chờ, đang chạy, tạm dừng hoặc bị gián đoạn. Tác vụ chạy lần lượt theo thứ tự thêm vào.",
        ))
        lay.addWidget(hint_label(
            "Tạm dừng có hiệu lực ngay với bước FFmpeg; với bước AI, tác vụ dừng ở điểm an toàn kế tiếp. "
            "Tác vụ 'Bị gián đoạn' (do ứng dụng đóng khi đang chạy) có thể bấm 'Chạy lại'."
        ))
        self.view = JobsView(services, ACTIVE_STATUSES | {JobStatus.INTERRUPTED})
        lay.addWidget(self.view, 1)

    def refresh(self) -> None:
        self.view.refresh()


class HistoryPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Lịch sử",
            "Các tác vụ đã kết thúc (hoàn thành, lỗi, đã hủy, bị gián đoạn) cùng log chi tiết.",
        ))
        self.view = JobsView(services, FINISHED_STATUSES)
        lay.addWidget(self.view, 1)

    def refresh(self) -> None:
        self.view.refresh()
