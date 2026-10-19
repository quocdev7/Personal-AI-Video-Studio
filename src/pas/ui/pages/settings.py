from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QSpinBox, QVBoxLayout,
    QWidget,
)

from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, hint_label, open_in_explorer, show_error

EXE_FILTER = "Chương trình (*.exe);;Tất cả (*.*)"


class SettingsPage(QWidget):
    settings_saved = Signal()

    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader("Cài đặt", "Đường dẫn công cụ, thư mục mặc định và cách xử lý tác vụ."))

        tools = QGroupBox("Công cụ")
        tf = QFormLayout(tools)
        self.ffmpeg = PathPicker("exe", EXE_FILTER, "Để trống để tự tìm")
        self.ffprobe = PathPicker("exe", EXE_FILTER, "Để trống để tự tìm")
        tf.addRow("ffmpeg.exe", self.ffmpeg)
        tf.addRow("ffprobe.exe", self.ffprobe)
        tf.addRow(hint_label("Khi để trống, ứng dụng tìm trong PATH, thư mục winget và tools\\ffmpeg\\bin."))
        lay.addWidget(tools)

        general = QGroupBox("Chung")
        gf = QFormLayout(general)
        self.output_dir = PathPicker("dir", placeholder="Để trống = cùng thư mục với file gốc (trong thư mục con)")
        self.max_jobs = QSpinBox()
        self.max_jobs.setRange(1, 4)
        self.min_disk = QDoubleSpinBox()
        self.min_disk.setRange(0, 1000)
        self.min_disk.setSuffix(" GB")
        self.keep_temp = QCheckBox("Giữ file tạm sau khi tác vụ hoàn thành (để chẩn đoán)")
        gf.addRow("Thư mục đầu ra mặc định", self.output_dir)
        gf.addRow("Số tác vụ chạy cùng lúc", self.max_jobs)
        gf.addRow("", hint_label("Máy không có GPU NVIDIA: nên để 1. Chạy nhiều tác vụ AI song song làm tất cả chậm hơn."))
        gf.addRow("Dung lượng trống tối thiểu", self.min_disk)
        gf.addRow("", self.keep_temp)
        lay.addWidget(general)

        row = QHBoxLayout()
        save = button("Lưu cài đặt", "primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        self.saved_label = QLabel()
        row.addWidget(self.saved_label)
        row.addStretch(1)
        lay.addLayout(row)

        data = QGroupBox("Dữ liệu ứng dụng")
        dl = QVBoxLayout(data)
        dl.addWidget(QLabel(f"Thư mục dữ liệu: {services.paths.root}"))
        drow = QHBoxLayout()
        b_logs = button("Mở thư mục log")
        b_data = button("Mở thư mục dữ liệu")
        b_clean = button("Dọn file tạm")
        b_logs.clicked.connect(lambda: open_in_explorer(self, services.paths.logs_dir))
        b_data.clicked.connect(lambda: open_in_explorer(self, services.paths.root))
        b_clean.clicked.connect(self._clean_temp)
        for b in (b_logs, b_data, b_clean):
            drow.addWidget(b)
        drow.addStretch(1)
        dl.addLayout(drow)
        dl.addWidget(hint_label("'Dọn file tạm' chỉ xóa file trung gian của các tác vụ đã kết thúc, "
                                "không bao giờ xóa file gốc hay kết quả."))
        lay.addWidget(data)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        s = self.services.settings
        self.ffmpeg.set_path(s.ffmpeg_path)
        self.ffprobe.set_path(s.ffprobe_path)
        self.output_dir.set_path(s.default_output_dir)
        self.max_jobs.setValue(s.max_concurrent_jobs)
        self.min_disk.setValue(s.min_free_disk_gb)
        self.keep_temp.setChecked(s.keep_temp_files)
        self.saved_label.clear()

    def _save(self) -> None:
        s = self.services.settings
        s.ffmpeg_path = self.ffmpeg.path()
        s.ffprobe_path = self.ffprobe.path()
        s.default_output_dir = self.output_dir.path()
        s.max_concurrent_jobs = self.max_jobs.value()
        s.min_free_disk_gb = self.min_disk.value()
        s.keep_temp_files = self.keep_temp.isChecked()
        try:
            self.services.save_settings()
        except OSError as exc:
            show_error(self, exc, "Không lưu được cài đặt")
            return
        self.saved_label.setText("Đã lưu.")
        self.settings_saved.emit()

    def _clean_temp(self) -> None:
        n = self.services.jobs.cleanup_finished_temp()
        QMessageBox.information(self, "Dọn file tạm", f"Đã xóa {n} thư mục tạm của các tác vụ đã kết thúc.")
