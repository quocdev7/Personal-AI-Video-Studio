from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QProgressBar, QTextBrowser, QVBoxLayout, QWidget

from pas.core.errors import PasError
from pas.jobs.models import FINISHED_STATUSES, JobEvent, JobStatus
from pas.modules.media_check.tasks import KIND, SUPPORTED_EXTENSIONS
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, format_duration, format_size, hint_label, open_in_explorer, show_error

FILE_FILTER = "Media ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS)))


class MediaCheckPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.job_id: int | None = None
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Kiểm tra media",
            "Đọc thông tin kỹ thuật của video/âm thanh (thời lượng, độ phân giải, tỷ lệ, FPS, luồng âm thanh) "
            "và cảnh báo các vấn đề thường gặp trước khi xử lý hoặc đăng.",
        ))

        box = QGroupBox("1. Chọn file")
        bl = QVBoxLayout(box)
        self.picker = PathPicker("file", FILE_FILTER)
        bl.addWidget(self.picker)
        bl.addWidget(hint_label("Có thể kéo thả file vào ô trên. File gốc chỉ được đọc, không bị thay đổi."))
        lay.addWidget(box)

        run_box = QGroupBox("2. Kiểm tra")
        rl = QVBoxLayout(run_box)
        row = QHBoxLayout()
        self.btn_start = button("Bắt đầu kiểm tra", "primary")
        self.btn_cancel = button("Hủy")
        self.btn_open = button("Mở thư mục chứa file")
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_open.clicked.connect(self._open_folder)
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

        res_box = QGroupBox("3. Kết quả")
        resl = QVBoxLayout(res_box)
        self.result = QTextBrowser()
        resl.addWidget(self.result)
        lay.addWidget(res_box, 1)
        self._update_buttons(None)

    def _start(self) -> None:
        path = self.picker.path()
        try:
            job = self.services.jobs.submit(KIND, f"Kiểm tra {Path(path).name}", {"path": path})
        except PasError as exc:
            show_error(self, exc, "Không thể bắt đầu")
            return
        self.job_id = job.id
        self.error.hide()
        self.result.clear()
        self.progress.setValue(0)
        self.status.setText("Đang chờ trong hàng đợi")
        self._update_buttons(JobStatus.QUEUED)

    def _cancel(self) -> None:
        if self.job_id is not None:
            self.services.jobs.cancel(self.job_id)

    def _open_folder(self) -> None:
        path = self.picker.path()
        if path:
            open_in_explorer(self, Path(path).parent)

    def _update_buttons(self, status: JobStatus | None) -> None:
        busy = status is not None and status not in FINISHED_STATUSES
        self.btn_start.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.btn_open.setEnabled(bool(self.picker.path()))

    def on_job_event(self, event: JobEvent) -> None:
        if event.job_id != self.job_id:
            return
        self.progress.setValue(int(event.progress * 100))
        self.status.setText(f"{event.status.label} — {event.message}" if event.message else event.status.label)
        self._update_buttons(event.status)
        if event.status not in FINISHED_STATUSES:
            return
        job = self.services.job_repo.get(event.job_id)
        if job.status == JobStatus.FAILED:
            self.error.setText(f"<b>{html.escape(job.error or '')}</b><br>{html.escape(job.error_hint or '')}")
            self.error.show()
        elif job.status == JobStatus.COMPLETED and job.result:
            self.result.setHtml(render_result(job.result))

    def refresh(self) -> None:
        self._update_buttons(self.services.job_repo.get(self.job_id).status if self.job_id else None)


def render_result(r: dict) -> str:
    e = html.escape
    rows = [
        ("File", e(r["path"])),
        ("Định dạng", e(r["format_name"])),
        ("Thời lượng", f"{format_duration(r['duration'])} ({r['duration']:.2f} giây)"),
        ("Dung lượng", format_size(r["size_bytes"])),
    ]
    if r["bit_rate"]:
        rows.append(("Bitrate tổng", f"{r['bit_rate'] / 1000:.0f} kb/s"))
    for v in r["video"]:
        rows.append((f"Video #{v['index']}", f"{e(v['codec'])}, {v['width']}×{v['height']}, {v['fps']:g} fps, tỷ lệ {e(r['aspect'])}"))
    for a in r["audio"]:
        lang = f", ngôn ngữ '{e(a['language'])}'" if a["language"] else ""
        rows.append((f"Âm thanh #{a['index']}", f"{e(a['codec'])}, {a['sample_rate']} Hz, {a['channels']} kênh{lang}"))
    if r["subtitle_streams"]:
        rows.append(("Phụ đề nhúng", f"{r['subtitle_streams']} luồng"))
    table = "".join(f"<tr><td style='padding:3px 12px 3px 0'><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows)
    if r["issues"]:
        issues = "<p style='color:#b42318'><b>Cảnh báo:</b></p><ul>" + "".join(f"<li>{e(i)}</li>" for i in r["issues"]) + "</ul>"
    else:
        issues = "<p style='color:#1d5e2e'><b>Không phát hiện vấn đề kỹ thuật cơ bản.</b></p>"
    return f"<table>{table}</table>{issues}"
