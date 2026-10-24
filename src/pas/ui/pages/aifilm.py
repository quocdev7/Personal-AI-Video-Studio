"""Trang "Phim ngắn AI": ý tưởng → kịch bản (Ollama, sửa được) → ảnh từng cảnh (LCM, CPU) → phim 9:16 lồng tiếng.

3 bước là 3 tác vụ trong hàng đợi, cùng một thư mục dự án '<tiêu đề> - phim AI'; trang chỉ giữ đường dẫn dự án.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QScrollArea, QSlider, QSpinBox, QTextBrowser,
    QVBoxLayout, QWidget,
)

from pas.core.errors import PasError
from pas.jobs.models import FINISHED_STATUSES, JobEvent, JobStatus
from pas.modules.aifilm import models, project, render, story
from pas.modules.aifilm.tasks import AUDIO_EXTENSIONS, KIND_IMAGES, KIND_RENDER, KIND_STORY
from pas.modules.safety import models as sf_models
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, format_duration, format_size, hint_label, open_in_explorer, show_error

MUSIC_FILTER = "Âm thanh ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS)))
STEP_NAMES = {KIND_STORY: "Bước 1", KIND_IMAGES: "Bước 2", KIND_RENDER: "Bước 3"}
PACKS = [models.LCM, *(v.pack for v in models.EN_VOICES.values())]
SCRIPT_HELP = (
    "Cách sửa kịch bản: 'NHÂN VẬT: tên | nam/nữ | giọng | ngoại hình (tiếng Anh)' — giọng là john, norman, kristin "
    "hoặc libritts_r#<số người đọc 0–903>. Mỗi cảnh: 'CẢNH n | chuyển động' (" + ", ".join(story.MOTIONS) + "), "
    "'NGƯỜI NÓI:' (để trống = cảnh không thoại), 'ẢNH:' mô tả hình bằng tiếng Anh (gọi nhân vật bằng tên), "
    "'EN:' lời thoại tiếng Anh, 'VI:' phụ đề. Dòng bắt đầu bằng # là ghi chú."
)


class AiFilmPage(QWidget):
    def __init__(self, services: AppServices):
        super().__init__()
        self.services = services
        self.job_id: int | None = None
        self.job_kind: str | None = None
        self.downloads: dict[int, str] = {}  # job id → khóa gói mô hình
        self.project_dir: str | None = None
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.addWidget(PageHeader(
            "Phim ngắn AI",
            "Từ một ý tưởng 1–3 câu: Ollama viết kịch bản nhiều nhân vật (thoại tiếng Anh + phụ đề Việt) → bạn sửa → "
            "vẽ ảnh từng cảnh bằng AI trên máy → ghép thành phim dọc 9:16 có chuyển động máy quay, mỗi nhân vật một "
            "giọng, phụ đề giữa khung. Chạy hoàn toàn trên máy, miễn phí. Phim có nhãn 'Nội dung tạo bằng AI'.",
        ))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 3)

        # ---- dự án + mô hình
        pbox = QGroupBox("Dự án và mô hình")
        pl = QVBoxLayout(pbox)
        row = QHBoxLayout()
        self.project_label = QLabel("Chưa có dự án — chạy bước 1 hoặc mở dự án cũ.")
        self.project_label.setWordWrap(True)
        self.btn_project = button("Mở dự án…")
        self.btn_project.clicked.connect(self._choose_project)
        self.btn_open = button("Mở thư mục dự án")
        self.btn_open.clicked.connect(lambda: open_in_explorer(self, self.project_dir))
        row.addWidget(self.project_label, 1)
        row.addWidget(self.btn_project)
        row.addWidget(self.btn_open)
        pl.addLayout(row)
        mrow = QHBoxLayout()
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.btn_models = button("Tải mô hình…")
        self.btn_models.clicked.connect(self._download_missing)
        mrow.addWidget(self.model_status, 1)
        mrow.addWidget(self.btn_models)
        pl.addLayout(mrow)
        lay.addWidget(pbox)

        # ---- bước 1
        b1 = QGroupBox("1. Viết kịch bản từ ý tưởng")
        f1 = QFormLayout(b1)
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText("vd: Ở học viện phép thuật, cô gái tóc bạc bị bắt nạt hóa ra là công chúa "
                                     "rồng bị phong ấn; hoàng tử kiêu ngạo tát cô và đánh thức sức mạnh của cô.")
        self.idea.setFixedHeight(70)
        self.shots = QSpinBox()
        self.shots.setRange(story.MIN_SHOTS, story.MAX_SHOTS)
        self.shots.setValue(story.DEFAULT_SHOTS)
        self.shots.setSuffix(" cảnh")
        self.out = PathPicker("dir", placeholder="Để trống = thư mục mặc định trong Cài đặt (hoặc Videos)")
        self.btn_story = button("Viết kịch bản", "primary")
        self.btn_story.clicked.connect(self._run_story)
        f1.addRow("Ý tưởng", self.idea)
        f1.addRow("Độ dài", self.shots)
        f1.addRow("Lưu dự án vào", self.out)
        f1.addRow(hint_label("Dùng mô hình Ollama trong Cài đặt (mặc định qwen2.5:7b), mất khoảng 2–6 phút trên CPU. "
                             "Mỗi cảnh 2–4 giây → 16 cảnh ≈ 45–60 giây phim."))
        f1.addRow(self.btn_story)
        lay.addWidget(b1)

        # ---- bước 2
        b2 = QGroupBox("2. Sửa kịch bản, vẽ ảnh từng cảnh")
        f2 = QFormLayout(b2)
        self.script = QPlainTextEdit()
        self.script.setPlaceholderText("Kịch bản từ bước 1 hiện ở đây — sửa thoại, phụ đề, mô tả ảnh tùy ý.")
        self.script.setMinimumHeight(220)
        self.regen = QLineEdit()
        self.regen.setPlaceholderText("vd: 2, 5 — vẽ lại ảnh các cảnh này với hạt giống mới (để trống = không)")
        self.fast = QCheckBox("Vẽ nhanh (4 bước thay vì 6 — nhanh hơn ~25%, ảnh kém chi tiết hơn)")
        self.btn_images = button("Vẽ ảnh", "primary")
        self.btn_images.clicked.connect(self._run_images)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListView.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(96, 171))
        self.gallery.setResizeMode(QListView.ResizeMode.Adjust)
        self.gallery.setMovement(QListView.Movement.Static)
        self.gallery.setFixedHeight(220)
        self.gallery.itemDoubleClicked.connect(lambda it: open_in_explorer(self, it.data(Qt.ItemDataRole.UserRole)))
        f2.addRow(self.script)
        f2.addRow(hint_label(SCRIPT_HELP))
        f2.addRow("Vẽ lại cảnh", self.regen)
        f2.addRow(self.fast)
        f2.addRow(hint_label("Chỉ vẽ cảnh mới hoặc đã đổi mô tả; ảnh cũ được giữ. Khoảng 30 giây/ảnh trên CPU "
                             "(16 cảnh ≈ 8 phút). Nhấp đúp ảnh để mở. Ảnh AI có thể sai tay/mặt — xem lại từng cảnh."))
        f2.addRow(self.btn_images)
        f2.addRow(self.gallery)
        lay.addWidget(b2)

        # ---- bước 3
        b3 = QGroupBox("3. Dựng phim")
        f3 = QFormLayout(b3)
        self.resolution = QComboBox()
        for k, (w, h) in render.RESOLUTIONS.items():
            self.resolution.addItem(f"{k} dọc ({w}×{h})", k)
        self.show_en = QCheckBox("Hiện cả dòng tiếng Anh nhỏ dưới phụ đề Việt")
        self.show_title = QCheckBox("Hiện tiêu đề ở đầu phim")
        self.show_title.setChecked(True)
        self.ai_label = QCheckBox(f"Nhãn '{render.AI_LABEL}' ở góc trên (khuyên dùng — nhiều nền tảng yêu cầu)")
        self.ai_label.setChecked(True)
        self.music = PathPicker("file", MUSIC_FILTER, "Không bắt buộc — nhạc bạn có quyền dùng")
        self.music.path_changed.connect(lambda _: self._sync())
        self.music_volume = QSlider(Qt.Orientation.Horizontal)
        self.music_volume.setRange(1, 100)
        self.music_volume.setValue(15)
        self.music_volume_label = QLabel()
        self.music_volume.valueChanged.connect(lambda v: self.music_volume_label.setText(f"{v}%"))
        self.music_volume_label.setText("15%")
        vrow = QHBoxLayout()
        vrow.addWidget(self.music_volume, 1)
        vrow.addWidget(self.music_volume_label)
        self.btn_render = button("Dựng phim", "primary")
        self.btn_render.clicked.connect(self._run_render)
        f3.addRow("Độ phân giải", self.resolution)
        f3.addRow(self.show_en)
        f3.addRow(self.show_title)
        f3.addRow(self.ai_label)
        f3.addRow("Nhạc nền", self.music)
        f3.addRow("Âm lượng nhạc", vrow)
        f3.addRow(hint_label("Giọng Piper tiếng Anh, mỗi nhân vật một giọng (theo cột giọng trong kịch bản). "
                             "Tạo 'phim.mp4' và 'ghi nguồn.txt' — đăng video dùng giọng libritts_r thì cần ghi nguồn."))
        f3.addRow(self.btn_render)
        lay.addWidget(b3)
        lay.addStretch(1)

        # ---- tiến độ + kết quả
        run = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.btn_cancel = button("Hủy")
        self.btn_cancel.clicked.connect(self._cancel)
        run.addWidget(self.progress, 1)
        run.addWidget(self.btn_cancel)
        outer.addLayout(run)
        self.status = QLabel("Chưa chạy")
        outer.addWidget(self.status)
        self.error = QLabel()
        self.error.setObjectName("errorBox")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)
        self.result = QTextBrowser()
        self.result.setOpenExternalLinks(False)
        outer.addWidget(self.result, 2)
        self._sync()

    # ------------------------------------------------------------ trạng thái

    def _missing_packs(self):
        mdir = self.services.paths.models_dir
        return [p for p in PACKS if not sf_models.is_installed(mdir, p)]

    def _busy(self) -> bool:
        if self.job_id is None:
            return False
        job = self.services.job_repo.get(self.job_id)
        return job is not None and job.status not in FINISHED_STATUSES

    def _sync(self) -> None:
        busy = self._busy()
        missing = self._missing_packs()
        try:
            models.require_runtime()
            runtime = ""
        except PasError as exc:
            runtime = f"{exc.message} {exc.hint or ''}"
        if self.downloads:
            text = f"Đang tải {len(self.downloads)} mô hình (xem trang Hàng đợi)…"
        elif missing:
            text = ("Chưa tải: " + ", ".join(p.key for p in missing)
                    + f" ({sum(p.size for p in missing) / 1e9:.2f} GB) — cần cho bước 2 và 3.")
        else:
            text = "Mô hình vẽ ảnh LCM Dreamshaper v7 + 4 giọng Piper tiếng Anh: đã có."
        self.model_status.setText(f"{runtime}\n{text}".strip())
        self.btn_models.setVisible(bool(missing))
        self.btn_models.setEnabled(not self.downloads)

        has_proj = bool(self.project_dir)
        has_images = has_proj and any((Path(self.project_dir) / project.IMAGES).glob("cảnh *.png"))
        self.btn_story.setEnabled(not busy)
        self.btn_images.setEnabled(not busy and has_proj and not runtime)
        self.btn_render.setEnabled(not busy and has_images)
        self.music_volume.setEnabled(bool(self.music.path()))
        self.btn_project.setEnabled(not busy)
        self.btn_open.setEnabled(has_proj)
        self.btn_cancel.setEnabled(busy)

    def set_project(self, path: str | Path, load_text: bool = True) -> None:
        pdir = Path(path)
        project.load(pdir)  # InvalidInput nếu không phải dự án
        self.project_dir = str(pdir)
        if load_text:
            try:
                self.script.setPlainText(project.read_script(pdir))
            except PasError:
                self.script.clear()
        done = [name for name, ok in (
            ("kịch bản", (pdir / project.SCRIPT_TXT).is_file()),
            ("ảnh", any((pdir / project.IMAGES).glob("cảnh *.png"))),
            ("phim", (pdir / project.VIDEO_MP4).is_file()),
        ) if ok]
        self.project_label.setText(f"<b>{html.escape(pdir.name)}</b><br>{html.escape(str(pdir.parent))}<br>"
                                   f"Đã có: {html.escape(', '.join(done) or 'chưa có gì')}")
        self._load_gallery()
        self._sync()

    def _load_gallery(self) -> None:
        self.gallery.clear()
        if not self.project_dir:
            return
        for f in sorted((Path(self.project_dir) / project.IMAGES).glob("cảnh *.png")):
            item = QListWidgetItem(QIcon(QPixmap(str(f)).scaled(96, 171, Qt.AspectRatioMode.KeepAspectRatio,
                                                                Qt.TransformationMode.SmoothTransformation)),
                                   f.stem.replace("cảnh ", "Cảnh "))
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self.gallery.addItem(item)

    def _choose_project(self) -> None:
        start = self.project_dir or self.out.path() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục '… - phim AI'", start)
        if chosen:
            try:
                self.set_project(chosen)
            except PasError as exc:
                show_error(self, exc, "Không mở được dự án")

    # ------------------------------------------------------------ chạy

    def _download_missing(self) -> None:
        missing = self._missing_packs()
        if not missing:
            return
        lines = "\n\n".join(f"• {p.title}\n  {p.size / 1e6:,.0f} MB — {p.license}\n  Nguồn: {p.source}"
                            + (f"\n  {p.note}" if p.note else "") for p in missing)
        answer = QMessageBox.question(
            self, "Tải mô hình",
            f"{lines}\n\nTổng: {sum(p.size for p in missing) / 1e9:.2f} GB. Chỉ tải một lần (cần mạng), kiểm tra mã "
            "SHA-256 rồi lưu vào thư mục dữ liệu ứng dụng. Sau đó chạy hoàn toàn trên máy. Tải ngay?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for pack in missing:
            try:
                job = self.services.jobs.submit(sf_models.DOWNLOAD_KIND, f"Tải {pack.key}", {"model": pack.key})
            except PasError as exc:
                show_error(self, exc, "Không thể tải")
                break
            self.downloads[job.id] = pack.key
        self._sync()

    def _submit(self, kind: str, title: str, params: dict) -> None:
        try:
            job = self.services.jobs.submit(kind, title, params)
        except PasError as exc:
            show_error(self, exc, "Không thể bắt đầu")
            return
        self.job_id, self.job_kind = job.id, kind
        self.error.hide()
        self.progress.setValue(0)
        self.status.setText(f"{STEP_NAMES[kind]}: đang chờ trong hàng đợi")
        self._sync()

    def _run_story(self) -> None:
        idea = self.idea.toPlainText().strip()
        self._submit(KIND_STORY, f"Phim AI: viết kịch bản '{idea[:40]}'", {
            "idea": idea, "shots": self.shots.value(), "output_dir": self.out.path(),
        })

    def _run_images(self) -> None:
        try:
            regen = [int(x) for x in self.regen.text().replace(",", " ").split()]
        except ValueError:
            show_error(self, PasError("Danh sách cảnh cần vẽ lại không hợp lệ.", "Ví dụ: 2, 5, 7"), "Không thể bắt đầu")
            return
        self._submit(KIND_IMAGES, f"Phim AI: vẽ ảnh {Path(self.project_dir).name}", {
            "project_dir": self.project_dir, "script": self.script.toPlainText(), "regenerate": regen,
            "fast": self.fast.isChecked(),
        })

    def _run_render(self) -> None:
        params = {"project_dir": self.project_dir, "script": self.script.toPlainText(),
                  "resolution": self.resolution.currentData(), "show_en": self.show_en.isChecked(),
                  "show_title": self.show_title.isChecked(), "ai_label": self.ai_label.isChecked()}
        if self.music.path():
            params["music"] = self.music.path()
            params["music_volume"] = self.music_volume.value() / 100
        self._submit(KIND_RENDER, f"Phim AI: dựng phim {Path(self.project_dir).name}", params)

    def _cancel(self) -> None:
        if self.job_id is not None:
            self.services.jobs.cancel(self.job_id)

    def on_job_event(self, event: JobEvent) -> None:
        if event.job_id in self.downloads and event.status in FINISHED_STATUSES:
            self.downloads.pop(event.job_id)
            job = self.services.job_repo.get(event.job_id)
            self._sync()
            if job.status == JobStatus.FAILED:
                show_error(self, PasError(job.error or "Tải mô hình thất bại", job.error_hint), "Không tải được mô hình")
            return
        if event.job_id != self.job_id:
            return
        step = STEP_NAMES.get(self.job_kind or "", "")
        self.progress.setValue(int(event.progress * 100))
        label = f"{event.status.label} — {event.message}" if event.message else event.status.label
        self.status.setText(f"{step}: {label}")
        if event.status in FINISHED_STATUSES:
            job = self.services.job_repo.get(event.job_id)
            if job.status == JobStatus.FAILED:
                self.error.setText(f"<b>{html.escape(job.error or '')}</b><br>{html.escape(job.error_hint or '')}")
                self.error.show()
            elif job.status == JobStatus.COMPLETED and job.result:
                r = job.result
                try:
                    # bước 2/3 lưu lại kịch bản đã chuẩn hóa → nạp lại để ô soạn khớp file
                    self.set_project(r["project_dir"])
                except PasError as exc:
                    self.error.setText(html.escape(exc.message))
                    self.error.show()
                self.result.setHtml(render_result(self.job_kind, r))
        self._sync()

    def refresh(self) -> None:
        self._sync()


def render_result(kind: str | None, r: dict) -> str:
    e = html.escape
    ok = "<p style='color:#1d5e2e'><b>{}</b></p>"
    if kind == KIND_STORY:
        out = ok.format(f"Bước 1 xong: '{e(r['title'])}' — {r['shots']} cảnh.")
        out += "<p>Nhân vật: " + e("; ".join(r["characters"])) + "</p>"
        return out + ("<p style='color:#8a5a00'>Kịch bản do máy viết — đọc lại, sửa thoại/phụ đề/mô tả ảnh ở ô bước 2 "
                      "rồi bấm 'Vẽ ảnh'.</p>")
    if kind == KIND_IMAGES:
        out = ok.format(f"Bước 2 xong: {len(r['images'])} ảnh (vẽ mới {r['generated']}, dùng lại {r['reused']}).")
        return out + ("<p style='color:#8a5a00'>Xem từng ảnh bên trên; cảnh nào chưa ưng thì ghi số cảnh vào "
                      "'Vẽ lại cảnh' hoặc sửa dòng 'ẢNH:' rồi bấm 'Vẽ ảnh' lần nữa.</p>")
    if kind == KIND_RENDER:
        out = ok.format(f"Bước 3 xong: {e(Path(r['video']).name)} — {r['width']}×{r['height']}, "
                        f"{format_duration(r['duration'])}, {format_size(r['size_bytes'])}, đã qua kiểm tra.")
        out += f"<p>{r['shots']} cảnh, {r['lines']} câu thoại. Ghi nguồn: {e(Path(r['credits']).name)}.</p>"
        return out + "<p style='color:#8a5a00'>Xem lại toàn bộ phim trước khi đăng.</p>"
    return ""
