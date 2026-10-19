from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from pas.core.errors import PasError
from pas.jobs.models import FINISHED_STATUSES, JobEvent, JobStatus
from pas.modules.clips.tasks import ASPECT_LABELS, FITS, KIND, MAX_SEGMENT, MIN_SEGMENT, VIDEO_EXTENSIONS
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, format_duration, format_size, hint_label, open_in_explorer, show_error

FILE_FILTER = "Video ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS)))


class ClipsPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.job_id: int | None = None
        self.output_dir: str | None = None
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Tạo clip ngắn",
            "Thả một video vào → tự cắt thành nhiều clip cùng độ dài, đổi khung (vd 9:16 cho TikTok/Shorts/Reels) "
            "và xuất MP4. Mỗi clip được kiểm tra lại sau khi xuất. File gốc không bị thay đổi.",
        ))

        box = QGroupBox("1. Chọn video")
        bl = QVBoxLayout(box)
        self.picker = PathPicker("file", FILE_FILTER)
        bl.addWidget(self.picker)
        lay.addWidget(box)

        s = services.settings
        opt = QGroupBox("2. Công thức")
        form = QFormLayout(opt)
        self.seconds = QSpinBox()
        self.seconds.setRange(MIN_SEGMENT, MAX_SEGMENT)
        self.seconds.setSuffix(" giây")
        self.seconds.setValue(s.clip_seconds)
        self.aspect = QComboBox()
        for key, label in ASPECT_LABELS.items():
            self.aspect.addItem(label, key)
        self.aspect.setCurrentIndex(max(0, self.aspect.findData(s.clip_aspect)))
        self.fit = QComboBox()
        for key, label in FITS.items():
            self.fit.addItem(label, key)
        self.fit.setCurrentIndex(max(0, self.fit.findData(s.clip_fit)))
        self.aspect.currentIndexChanged.connect(self._sync_fit)
        self.out = PathPicker("dir", placeholder="Để trống = cạnh video gốc (hoặc thư mục mặc định trong Cài đặt)")
        form.addRow("Độ dài mỗi clip", self.seconds)
        form.addRow("Khung hình", self.aspect)
        form.addRow("Lấp khung", self.fit)
        form.addRow("Lưu vào", self.out)
        form.addRow(hint_label(
            "Clip cuối ngắn hơn 30% độ dài clip sẽ được gộp vào clip trước. Kết quả nằm trong thư mục mới "
            "'<tên video> - clip 9x16'. Trên máy không có GPU, mã hóa mất khoảng 0,5–2 lần thời lượng video."
        ))
        lay.addWidget(opt)
        self._sync_fit()

        run_box = QGroupBox("3. Tạo")
        rl = QVBoxLayout(run_box)
        row = QHBoxLayout()
        self.btn_start = button("Tạo clip", "primary")
        self.btn_cancel = button("Hủy")
        self.btn_open = button("Mở thư mục kết quả")
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_open.clicked.connect(lambda: open_in_explorer(self, self.output_dir))
        for b in (self.btn_start, self.btn_cancel, self.btn_open):
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Chưa chạy")
        rl.addWidget(self.progress)
        rl.addWidget(self.status)
        lay.addWidget(run_box)

        self.error = QLabel()
        self.error.setObjectName("errorBox")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)

        self.result = QTextBrowser()
        lay.addWidget(self.result, 1)
        self._update_buttons(None)

    def _sync_fit(self) -> None:
        self.fit.setEnabled(self.aspect.currentData() != "original")

    def _start(self) -> None:
        path = self.picker.path()
        params = {
            "path": path,
            "segment_seconds": self.seconds.value(),
            "aspect": self.aspect.currentData(),
            "fit": self.fit.currentData(),
            "output_dir": self.out.path(),
        }
        try:
            job = self.services.jobs.submit(KIND, f"Tạo clip {Path(path).name}", params)
        except PasError as exc:
            show_error(self, exc, "Không thể bắt đầu")
            return
        s = self.services.settings
        s.clip_seconds, s.clip_aspect, s.clip_fit = params["segment_seconds"], params["aspect"], params["fit"]
        try:
            self.services.save_settings()
        except OSError:  # chỉ là ghi nhớ lựa chọn; tác vụ vẫn chạy
            pass
        self.job_id = job.id
        self.output_dir = None
        self.error.hide()
        self.result.clear()
        self.progress.setValue(0)
        self.status.setText("Đang chờ trong hàng đợi")
        self._update_buttons(JobStatus.QUEUED)

    def _cancel(self) -> None:
        if self.job_id is not None:
            self.services.jobs.cancel(self.job_id)

    def _update_buttons(self, status: JobStatus | None) -> None:
        busy = status is not None and status not in FINISHED_STATUSES
        self.btn_start.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.btn_open.setEnabled(bool(self.output_dir))

    def on_job_event(self, event: JobEvent) -> None:
        if event.job_id != self.job_id:
            return
        self.progress.setValue(int(event.progress * 100))
        self.status.setText(f"{event.status.label} — {event.message}" if event.message else event.status.label)
        if event.status in FINISHED_STATUSES:
            job = self.services.job_repo.get(event.job_id)
            if job.status == JobStatus.FAILED:
                self.error.setText(f"<b>{html.escape(job.error or '')}</b><br>{html.escape(job.error_hint or '')}")
                self.error.show()
            elif job.status == JobStatus.COMPLETED and job.result:
                self.output_dir = job.result["output_dir"]
                self.result.setHtml(render_result(job.result))
        self._update_buttons(event.status)

    def refresh(self) -> None:
        self._update_buttons(self.services.job_repo.get(self.job_id).status if self.job_id else None)


def render_result(r: dict) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0'>{e(Path(f['path']).name)}</td>"
        f"<td style='padding-right:12px'>từ {format_duration(f['start'])}</td>"
        f"<td style='padding-right:12px'>{f['duration']:.1f} s</td><td>{format_size(f['size_bytes'])}</td></tr>"
        for f in r["files"]
    )
    total = sum(f["size_bytes"] for f in r["files"])
    return (f"<p style='color:#1d5e2e'><b>Đã tạo {len(r['files'])} clip ({format_size(total)}), "
            f"tất cả đã qua kiểm tra.</b></p><p>Thư mục: {e(r['output_dir'])}</p><table>{rows}</table>")
