from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QSpinBox, QTextBrowser,
    QVBoxLayout, QWidget,
)

from pas.core.errors import PasError
from pas.jobs.models import FINISHED_STATUSES, JobEvent, JobStatus
from pas.modules.clips.tasks import ASPECT_LABELS, FITS, KIND, MAX_SEGMENT, MIN_SEGMENT, VIDEO_EXTENSIONS
from pas.modules.safety import models as sf_models
from pas.modules.safety.report import fmt_time
from pas.modules.safety.scan import KINDS
from pas.modules.safety.words import ensure_user_list
from pas.modules.vietsub.ocr import REGIONS
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, format_duration, format_size, hint_label, open_in_explorer, show_error

FILE_FILTER = "Video ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS)))


class ClipsPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.job_id: int | None = None
        self.download_id: int | None = None
        self.output_dir: str | None = None
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(PageHeader(
            "Tạo clip ngắn",
            "Thả một video vào → tự cắt thành nhiều clip cùng độ dài, đổi khung (vd 9:16 cho TikTok/Shorts/Reels), "
            "tùy chọn Việt hóa phụ đề tiếng Trung in trên hình và làm mờ nội dung nhạy cảm, rồi xuất MP4. "
            "Mỗi clip được kiểm tra lại sau khi xuất. "
            "File gốc không bị thay đổi.",
        ))

        box = QGroupBox("1. Chọn video")
        bl = QVBoxLayout(box)
        self.picker = PathPicker("file", FILE_FILTER)
        bl.addWidget(self.picker)
        lay.addWidget(box)

        s = services.settings
        opt = QGroupBox("2. Công thức")
        form = QFormLayout(opt)
        self.split = QCheckBox("Cắt thành nhiều clip")
        self.split.setChecked(s.clip_split)
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
        self.split.toggled.connect(self._sync_fit)
        self.vietsub = QCheckBox("Việt hóa phụ đề: đọc chữ Trung in trên hình → dịch sang tiếng Việt → làm mờ chữ Trung")
        self.vietsub.setChecked(s.clip_vietsub)
        self.region = QComboBox()
        for key, label in REGIONS.items():
            self.region.addItem(label, key)
        self.region.setCurrentIndex(max(0, self.region.findData(s.clip_vietsub_region)))
        self.vietsub.toggled.connect(self._sync_fit)
        self.safety = QCheckBox("Kiểm duyệt: tự làm mờ nội dung dễ vi phạm tiêu chuẩn cộng đồng (Facebook…) + báo cáo")
        self.safety.setChecked(s.clip_safety)
        self.safety.toggled.connect(self._sync_fit)
        self.safety_kinds = {k: QCheckBox(label) for k, label in KINDS.items()}
        for k, cb in self.safety_kinds.items():
            cb.setChecked(getattr(s, f"clip_safety_{k}"))
            cb.toggled.connect(self._sync_fit)
        kinds_row = QHBoxLayout()
        for cb in self.safety_kinds.values():
            kinds_row.addWidget(cb)
        kinds_row.addStretch(1)
        tools_row = QHBoxLayout()
        self.btn_model = button("Tải mô hình bạo lực…")
        self.btn_model.clicked.connect(self._download_model)
        self.btn_words = button("Sửa danh sách từ cấm…")
        self.btn_words.clicked.connect(self._edit_words)
        self.model_status = QLabel()
        tools_row.addWidget(self.btn_model)
        tools_row.addWidget(self.btn_words)
        tools_row.addWidget(self.model_status, 1)
        self.out = PathPicker("dir", placeholder="Để trống = cạnh video gốc (hoặc thư mục mặc định trong Cài đặt)")
        form.addRow("", self.split)
        form.addRow("Độ dài mỗi clip", self.seconds)
        form.addRow("Khung hình", self.aspect)
        form.addRow("Lấp khung", self.fit)
        form.addRow("", self.vietsub)
        form.addRow("Tìm phụ đề ở", self.region)
        form.addRow("", self.safety)
        form.addRow("Che", kinds_row)
        form.addRow("", tools_row)
        form.addRow("Lưu vào", self.out)
        form.addRow(hint_label(
            "Clip cuối ngắn hơn 30% độ dài clip sẽ được gộp vào clip trước. Kết quả nằm trong thư mục mới "
            "'<tên video> - clip 9x16'. Trên máy không có GPU, mã hóa mất khoảng 0,5–2 lần thời lượng video."
        ))
        form.addRow(hint_label(
            "Việt hóa phụ đề cần Ollama và mô hình qwen2.5:7b chạy trên máy này (không gửi dữ liệu ra ngoài). "
            "Bản dịch máy có thể sai — xem lại file '<tên video> - vi.srt' trước khi đăng. Trên CPU, dịch khoảng "
            "3–5 giây mỗi câu. Chữ đứng yên quá 20 giây (logo) chỉ được làm mờ, không dịch."
        ))
        form.addRow(hint_label(
            "Kiểm duyệt chạy trên máy: khỏa thân (NudeNet) che đúng vùng; bạo lực / máu me (CLIP) che cả khung; "
            "chữ thô tục trên hình (theo danh sách từ, sửa được) che dòng chữ và thay bằng *** trong phụ đề. "
            "Nhận diện tự động có thể sót hoặc che nhầm và KHÔNG bảo đảm qua được kiểm duyệt của Facebook — "
            "xem file '<tên video> - kiểm duyệt.txt' và xem lại video trước khi đăng."
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
        self.seconds.setEnabled(self.split.isChecked())
        self.region.setEnabled(self.vietsub.isChecked())
        on = self.safety.isChecked()
        for cb in self.safety_kinds.values():
            cb.setEnabled(on)
        self.btn_words.setEnabled(on)
        self._sync_model()

    def _sync_model(self) -> None:
        m = sf_models.CLIP_VISION
        installed = sf_models.is_installed(self.services.paths.models_dir, m)
        downloading = self.download_id is not None
        self.btn_model.setVisible(not installed)
        self.btn_model.setEnabled(not downloading and self.safety.isChecked())
        needed = self.safety.isChecked() and self.safety_kinds["violence"].isChecked()
        if installed:
            self.model_status.setText("Mô hình bạo lực: đã có")
        elif downloading:
            self.model_status.setText("Đang tải mô hình bạo lực (xem trang Tác vụ)…")
        else:
            self.model_status.setText(f"Mô hình bạo lực chưa tải ({m.size / 1e6:.0f} MB)"
                                      + (" — cần tải, hoặc bỏ chọn mục bạo lực" if needed else ""))

    def _download_model(self) -> None:
        m = sf_models.CLIP_VISION
        answer = QMessageBox.question(
            self, "Tải mô hình",
            f"{m.title}\n\nDung lượng: {m.size / 1e6:.0f} MB\nGiấy phép: {m.license}\nNguồn: {m.source}\n\n"
            "Chỉ tải một lần (cần mạng), kiểm tra mã SHA-256 rồi lưu vào thư mục dữ liệu ứng dụng. "
            "Sau đó chạy hoàn toàn trên máy. Tải ngay?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            job = self.services.jobs.submit(sf_models.DOWNLOAD_KIND, "Tải mô hình bạo lực (CLIP)", {"model": m.key})
        except PasError as exc:
            show_error(self, exc, "Không thể tải")
            return
        self.download_id = job.id
        self._sync_model()

    def _edit_words(self) -> None:
        try:
            path = ensure_user_list(self.services.paths.root)
        except OSError as exc:
            show_error(self, PasError(f"Không tạo được file danh sách từ: {exc}"), "Có lỗi")
            return
        open_in_explorer(self, path)

    def _start(self) -> None:
        path = self.picker.path()
        params = {
            "path": path,
            "segment_seconds": self.seconds.value() if self.split.isChecked() else 0,
            "aspect": self.aspect.currentData(),
            "fit": self.fit.currentData(),
            "output_dir": self.out.path(),
            "vietsub": self.vietsub.isChecked(),
            "vietsub_region": self.region.currentData(),
        }
        if self.safety.isChecked():
            params["safety"] = {k: cb.isChecked() for k, cb in self.safety_kinds.items()}
        try:
            job = self.services.jobs.submit(KIND, f"Tạo clip {Path(path).name}", params)
        except PasError as exc:
            show_error(self, exc, "Không thể bắt đầu")
            return
        s = self.services.settings
        if self.split.isChecked():
            s.clip_seconds = params["segment_seconds"]
        s.clip_split, s.clip_aspect, s.clip_fit = self.split.isChecked(), params["aspect"], params["fit"]
        s.clip_vietsub, s.clip_vietsub_region = params["vietsub"], params["vietsub_region"]
        s.clip_safety = self.safety.isChecked()
        for k, cb in self.safety_kinds.items():
            setattr(s, f"clip_safety_{k}", cb.isChecked())
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
        if event.job_id == self.download_id and event.status in FINISHED_STATUSES:
            self.download_id = None
            job = self.services.job_repo.get(event.job_id)
            self._sync_model()
            if job.status == JobStatus.FAILED:
                show_error(self, PasError(job.error or "Tải mô hình thất bại", job.error_hint), "Không tải được mô hình")
            return
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
    out = (f"<p style='color:#1d5e2e'><b>Đã tạo {len(r['files'])} clip ({format_size(total)}), "
           f"tất cả đã qua kiểm tra.</b></p><p>Thư mục: {e(r['output_dir'])}</p>")
    vs = r.get("vietsub")
    if vs:
        if not vs["lines"]:
            out += "<p style='color:#8a5a00'><b>Không tìm thấy phụ đề tiếng Trung nào</b> trong vùng đã chọn — video giữ nguyên chữ.</p>"
        else:
            out += (f"<p>Việt hóa: dịch {vs['translated']}/{vs['lines']} câu bằng {e(vs['model'])}"
                    + (f", {vs['static']} chữ đứng yên chỉ làm mờ" if vs["static"] else "")
                    + f". Phụ đề: {e(Path(vs['srt_vi']).name)}, {e(Path(vs['srt_zh']).name)}.</p>"
                    "<p style='color:#8a5a00'>Bản dịch máy — nên xem lại trước khi đăng.</p>")
        if vs["failed"]:
            out += ("<p style='color:#a11'><b>Không dịch được (chỉ làm mờ):</b> "
                    + e("; ".join(vs["failed"][:10])) + ("…" if len(vs["failed"]) > 10 else "") + "</p>")
    sf = r.get("safety")
    if sf:
        n = len(sf["intervals"])
        if not n:
            out += "<p>Kiểm duyệt: không phát hiện đoạn nào cần che.</p>"
        else:
            parts = ", ".join(f"{KINDS[k]} {c} đoạn" for k, c in sf["counts"].items() if c)
            items = "".join(f"<li>{fmt_time(i['start'])}–{fmt_time(i['end'])} {e(KINDS[i['kind']])}: "
                            f"{e(', '.join(i['labels']))}</li>" for i in sf["intervals"][:15])
            more = f"<li>… và {n - 15} đoạn khác</li>" if n > 15 else ""
            out += (f"<p style='color:#8a5a00'><b>Kiểm duyệt: đã làm mờ {n} đoạn</b> ({e(parts)}).</p>"
                    f"<ul>{items}{more}</ul>")
        out += (f"<p>Báo cáo: {e(Path(sf['report']).name)}. Nhận diện tự động có thể sót/che nhầm, "
                "không bảo đảm qua kiểm duyệt của nền tảng — hãy xem lại video.</p>")
    return out + f"<table>{rows}</table>"
