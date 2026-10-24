from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QStackedWidget, QWidget

from pas import APP_NAME, __version__
from pas.jobs.models import JobEvent
from pas.services import AppServices
from pas.ui.bridge import JobEventBridge
from pas.ui.pages.clips import ClipsPage
from pas.ui.pages.dashboard import DashboardPage
from pas.ui.pages.jobs_pages import HistoryPage, QueuePage
from pas.ui.pages.media_check import MediaCheckPage
from pas.ui.pages.projects import ProjectsPage
from pas.ui.pages.aifilm import AiFilmPage
from pas.ui.pages.remake import RemakePage
from pas.ui.pages.settings import SettingsPage

log = logging.getLogger(__name__)

# Chức năng chưa có backend: hiển thị mờ, không bấm được, ghi rõ giai đoạn.
PLANNED_PAGES = [
    ("Video → phụ đề", "Giai đoạn 3"),
    ("Chia theo cảnh / nội dung", "Giai đoạn 4–5"),
    ("Xử lý âm thanh", "Giai đoạn 6"),
    ("Dịch & lồng tiếng", "Giai đoạn 6"),
]


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self._closed = False
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1200, 780)

        self.dashboard = DashboardPage(services)
        self.projects = ProjectsPage(services)
        self.media_check = MediaCheckPage(services)
        self.clips = ClipsPage(services)
        self.remake = RemakePage(services)
        self.aifilm = AiFilmPage(services)
        self.queue = QueuePage(services)
        self.history = HistoryPage(services)
        self.settings = SettingsPage(services)
        pages = [
            ("Tổng quan", self.dashboard),
            ("Tạo clip ngắn", self.clips),
            ("Làm lại video", self.remake),
            ("Phim ngắn AI", self.aifilm),
            ("Dự án", self.projects),
            ("Kiểm tra media", self.media_check),
            ("Hàng đợi", self.queue),
            ("Lịch sử", self.history),
            ("Cài đặt", self.settings),
        ]

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(220)
        self.stack = QStackedWidget()
        for title, page in pages:
            self.nav.addItem(title)
            self.stack.addWidget(page)
        for title, phase in PLANNED_PAGES:
            item = QListWidgetItem(f"{title}  ({phase})")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setToolTip("Chưa triển khai — sẽ có ở " + phase)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._switch)
        self.nav.setCurrentRow(0)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.dashboard.go_to_queue.connect(lambda: self.nav.setCurrentRow(pages.index(("Hàng đợi", self.queue))))
        self.settings.settings_saved.connect(self.dashboard.refresh_environment)

        # Sự kiện từ worker thread → signal → luồng UI. Gộp nhiều sự kiện thành một lần làm mới bảng.
        self.bridge = JobEventBridge()
        self.bridge.job_changed.connect(self._on_job_event)
        services.jobs.add_listener(self.bridge)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._refresh_current)
        self._refresh_current()

    def _switch(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self._refresh_current()

    def _refresh_current(self) -> None:
        if self._closed:
            return
        page = self.stack.currentWidget()
        refresh = getattr(page, "refresh", None)
        if refresh:
            refresh()

    def _on_job_event(self, event: JobEvent) -> None:
        if self._closed:  # sự kiện queued đã nằm trong hàng đợi Qt trước khi ngắt kết nối
            return
        self.media_check.on_job_event(event)
        self.clips.on_job_event(event)
        self.remake.on_job_event(event)
        self.aifilm.on_job_event(event)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        running = self.services.jobs.running_ids()
        if running:
            answer = QMessageBox.question(
                self, "Đóng ứng dụng",
                f"Đang có {len(running)} tác vụ chạy. Đóng bây giờ sẽ dừng tác vụ và đánh dấu 'Bị gián đoạn' "
                "(có thể chạy lại lần sau). File gốc không bị ảnh hưởng.\n\nVẫn đóng?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        """Ngắt UI khỏi dịch vụ trước khi đóng CSDL: sự kiện phát ra lúc dừng tác vụ không được chạm CSDL đã đóng."""
        if self._closed:
            return
        self._closed = True
        log.info("Đóng ứng dụng")
        self._refresh_timer.stop()
        self.bridge.job_changed.disconnect(self._on_job_event)
        self.services.close()
