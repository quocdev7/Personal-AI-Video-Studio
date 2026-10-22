"""Trang "Làm lại video": 4 bước chạy tuần tự trên cùng một thư mục dự án '<tên video> - làm lại'.

Mỗi bước là một tác vụ riêng trong hàng đợi (chạy lại được, có tiến độ, hủy được); trang chỉ giữ đường dẫn dự án.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QScrollArea, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from pas.core.errors import PasError
from pas.jobs.models import FINISHED_STATUSES, JobEvent, JobStatus
from pas.modules.clips.tasks import ASPECT_LABELS, FITS, VIDEO_EXTENSIONS
from pas.modules.remake import asr, edit, models, project, script, tts, vieneu_tts
from pas.modules.remake.tasks import KIND_EDIT, KIND_SCRIPT, KIND_SEO, KIND_VOICE, MAX_CREDIT, MAX_TITLE
from pas.modules.safety import models as sf_models
from pas.services import AppServices
from pas.ui.widgets import PageHeader, PathPicker, button, format_duration, format_size, hint_label, open_in_explorer, show_error

FILE_FILTER = "Video ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS)))
SAMPLE_FILTER = "Âm thanh/video ({});;Tất cả (*.*)".format(" ".join(f"*{e}" for e in sorted(vieneu_tts.SAMPLE_EXTENSIONS)))
CLONE = ""  # mục "nhân bản từ file mẫu" trong danh sách giọng VieNeu
STEP_NAMES = {KIND_SCRIPT: "Bước 1", KIND_VOICE: "Bước 2", KIND_EDIT: "Bước 3", KIND_SEO: "Bước 4"}


class RemakePage(QWidget):
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
            "Làm lại video",
            "Từ một video có lời thoại: lấy transcript gốc → dịch theo ngữ cảnh → viết lại lời kể tiếng Việt có hook mở đầu "
            "→ đọc bằng giọng mới → dựng lại video (cắt cảnh ngắn, đổi thứ tự, zoom/crop, chữ, khớp giọng mới) "
            "→ thumbnail + tiêu đề + từ khóa. Chạy trên máy (Whisper, Ollama, VieNeu/Piper). File gốc không bị thay đổi. "
            "Làm lại KHÔNG xóa bản quyền của video gốc — hãy ghi nguồn và chỉ dùng video bạn có quyền.",
        ))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 3)

        # ---- dự án
        pbox = QGroupBox("Dự án")
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
        self.credit = QLineEdit()
        self.credit.setMaxLength(MAX_CREDIT)
        self.credit.setPlaceholderText("vd: Nguồn: kênh ABC (Bilibili) — dùng ở bước 3 (chữ trên video) và bước 4 (SEO)")
        cf = QFormLayout()
        cf.addRow("Ghi nguồn video gốc", self.credit)
        pl.addLayout(cf)
        lay.addWidget(pbox)

        # ---- bước 1
        b1 = QGroupBox("1. Lấy SUB && viết lại nội dung")
        f1 = QFormLayout(b1)
        self.picker = PathPicker("file", FILE_FILTER)
        self.language = QComboBox()
        for k, label in asr.LANGUAGES.items():
            self.language.addItem(label, k)
        self.style = QComboBox()
        for k, label in script.STYLES.items():
            self.style.addItem(label, k)
        self.out = PathPicker("dir", placeholder="Để trống = cạnh video gốc (hoặc thư mục mặc định trong Cài đặt)")
        self.whisper_status = QLabel()
        self.btn_whisper = button("Tải mô hình Whisper…")
        self.btn_whisper.clicked.connect(lambda: self._download(models.WHISPER))
        wrow = QHBoxLayout()
        wrow.addWidget(self.whisper_status, 1)
        wrow.addWidget(self.btn_whisper)
        self.btn_script = button("Chạy bước 1", "primary")
        self.btn_script.clicked.connect(self._run_script)
        f1.addRow("Video", self.picker)
        f1.addRow("Ngôn ngữ lời thoại", self.language)
        f1.addRow("Văn phong", self.style)
        f1.addRow("Lưu dự án vào", self.out)
        f1.addRow(wrow)
        f1.addRow(hint_label("Tạo: 'transcript gốc.srt', 'bản dịch.srt', 'kịch bản.txt'. Dịch/viết lại bằng mô hình "
                             f"Ollama trong Cài đặt — bản máy, cần đọc lại. Video 10 phút mất khoảng 10–20 phút trên CPU."))
        f1.addRow(self.btn_script)
        lay.addWidget(b1)

        # ---- bước 2
        b2 = QGroupBox("2. Tạo giọng đọc mới")
        f2 = QFormLayout(b2)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Kịch bản từ bước 1 hiện ở đây — sửa tùy ý, hoặc dán kịch bản của bạn. "
                                     "Mỗi đoạn cách nhau một dòng trống.")
        self.text.setMinimumHeight(160)
        self.voice = QComboBox()
        self.voice.addItem("VieNeu v3 Turbo — giọng tự nhiên, có giọng có sẵn hoặc nhân bản từ file mẫu (Apache-2.0)",
                           vieneu_tts.KEY)
        for k, v in models.VOICES.items():
            self.voice.addItem(f"Piper {k} — {v.label}", k)
        self.voice.currentIndexChanged.connect(self._sync)
        self.preset = QComboBox()
        for pr in vieneu_tts.presets():
            self.preset.addItem(f"{pr.name} — {pr.description}" if pr.description else pr.name, pr.name)
        self.preset.addItem("Nhân bản từ file mẫu…", CLONE)
        self.preset.currentIndexChanged.connect(self._sync)
        self.sample = PathPicker("file", SAMPLE_FILTER, "File 3–8 giây, một người nói rõ, không nhạc nền")
        self.sample.path_changed.connect(lambda _: self._sync())
        self.speaker = QSpinBox()
        self.tone = QComboBox()
        for k, t in tts.TONES.items():
            self.tone.addItem(t.label, k)
        self.voice_status = QLabel()
        self.voice_status.setWordWrap(True)
        self.btn_voice_dl = button("Tải giọng đọc…")
        self.btn_voice_dl.clicked.connect(lambda: self._download(self._voice_pack()))
        vrow = QHBoxLayout()
        vrow.addWidget(self.voice_status, 1)
        vrow.addWidget(self.btn_voice_dl)
        self.btn_voice = button("Chạy bước 2", "primary")
        self.btn_voice.clicked.connect(self._run_voice)
        f2.addRow("Kịch bản", self.text)
        f2.addRow("Giọng", self.voice)
        f2.addRow("Giọng VieNeu", self.preset)
        f2.addRow("File giọng mẫu", self.sample)
        f2.addRow("Người đọc", self.speaker)
        f2.addRow("Tông giọng", self.tone)
        f2.addRow(vrow)
        self.clone_hint = hint_label(vieneu_tts.CLONE_NOTE + " File mẫu chỉ được đọc, không bị thay đổi.")
        f2.addRow(self.clone_hint)
        f2.addRow(hint_label("Xuất riêng 'giọng đọc.wav'. VieNeu đọc chậm hơn Piper (kịch bản 5 phút ≈ 6 phút trên CPU); "
                             "với VieNeu, tông giọng chỉ đổi khoảng nghỉ. Khi đăng video, ghi nguồn giọng đọc "
                             "(có sẵn trong SEO.txt ở bước 4)."))
        f2.addRow(self.btn_voice)
        self.f2 = f2
        lay.addWidget(b2)

        # ---- bước 3
        b3 = QGroupBox("3. Dựng lại video theo giọng mới")
        f3 = QFormLayout(b3)
        self.aspect = QComboBox()
        for k, label in ASPECT_LABELS.items():
            self.aspect.addItem(label, k)
        self.aspect.currentIndexChanged.connect(self._sync)
        self.fit = QComboBox()
        for k, label in FITS.items():
            self.fit.addItem(label, k)
        self.zoom = QComboBox()
        for k, label in edit.ZOOM_LABELS.items():
            self.zoom.addItem(label, k)
        self.captions = QCheckBox("Thêm phụ đề lời đọc mới")
        self.captions.setChecked(True)
        self.blur_subs = QCheckBox("Làm mờ chữ/phụ đề gốc ở đáy hình")
        self.blur_subs.setChecked(True)
        self.bg_volume = QSpinBox()
        self.bg_volume.setRange(0, 100)
        self.bg_volume.setSuffix(" %")
        self.title = QLineEdit()
        self.title.setMaxLength(MAX_TITLE)
        self.title.setPlaceholderText("Chữ lớn hiện trong phần mở đầu (để trống = không có)")
        self.btn_edit = button("Chạy bước 3", "primary")
        self.btn_edit.clicked.connect(self._run_edit)
        f3.addRow("Khung hình", self.aspect)
        f3.addRow("Lấp khung", self.fit)
        f3.addRow("Zoom", self.zoom)
        f3.addRow(self.captions)
        f3.addRow(self.blur_subs)
        f3.addRow("Giữ tiếng gốc", self.bg_volume)
        f3.addRow("Tiêu đề trên video", self.title)
        f3.addRow(hint_label("Cảnh được cắt ngắn (1,2–3 s), sắp lại theo nội dung từng đoạn lời đọc, cảnh hook đưa lên đầu; "
                             "độ dài video = độ dài giọng đọc. Kết quả: 'video mới.mp4' (đã kiểm tra lại sau khi xuất)."))
        f3.addRow(self.btn_edit)
        lay.addWidget(b3)

        # ---- bước 4
        b4 = QGroupBox("4. Thumbnail + SEO")
        f4 = QFormLayout(b4)
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        self.notes.setPlaceholderText("Ghi chú cho mô hình (vd: kênh về ẩm thực, người xem 18–30 tuổi) — không bắt buộc")
        self.btn_seo = button("Chạy bước 4", "primary")
        self.btn_seo.clicked.connect(self._run_seo)
        f4.addRow("Ghi chú", self.notes)
        f4.addRow(hint_label("Tạo 3 thumbnail 1280×720 (khung hình rõ nét nhất + chữ lớn), 5 tiêu đề, mô tả, từ khóa, "
                             "hashtag vào 'SEO.txt'. Từ khóa do mô hình cục bộ đề xuất — KHÔNG có số liệu lượng tìm kiếm."))
        f4.addRow(self.btn_seo)
        lay.addWidget(b4)
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

    def _voice_pack(self):
        key = self.voice.currentData()
        return models.VIENEU if key == vieneu_tts.KEY else models.VOICES[key].pack

    def _busy(self) -> bool:
        if self.job_id is None:
            return False
        job = self.services.job_repo.get(self.job_id)
        return job is not None and job.status not in FINISHED_STATUSES

    def _sync(self) -> None:
        mdir = self.services.paths.models_dir
        downloading = set(self.downloads.values())
        busy = self._busy()
        has_whisper = sf_models.is_installed(mdir, models.WHISPER)
        self.whisper_status.setText("Whisper large-v3-turbo: đã có" if has_whisper else
                                    "Đang tải Whisper (xem trang Hàng đợi)…" if models.WHISPER.key in downloading else
                                    f"Chưa tải Whisper large-v3-turbo ({models.WHISPER.size / 1e9:.1f} GB) — cần cho bước 1")
        self.btn_whisper.setVisible(not has_whisper)
        self.btn_whisper.setEnabled(models.WHISPER.key not in downloading)

        is_vieneu = self.voice.currentData() == vieneu_tts.KEY
        cloning = is_vieneu and self.preset.currentData() == CLONE
        self.f2.setRowVisible(self.preset, is_vieneu)
        self.f2.setRowVisible(self.sample, cloning)
        self.f2.setRowVisible(self.clone_hint, cloning)
        self.f2.setRowVisible(self.speaker, not is_vieneu)
        pack = self._voice_pack()
        name = "VieNeu v3 Turbo" if is_vieneu else f"giọng {self.voice.currentData()}"
        if not is_vieneu:
            v = models.VOICES[self.voice.currentData()]
            self.speaker.setRange(0, v.speakers - 1)
            self.speaker.setEnabled(v.speakers > 1)
        has_voice = sf_models.is_installed(mdir, pack)
        lib_ok = not is_vieneu or vieneu_tts.available()
        lic = f"Giấy phép: {pack.license}."
        self.voice_status.setText(f"Chưa cài thư viện VieNeu — {vieneu_tts.INSTALL_HINT}" if not lib_ok else
                                  f"{name}: đã có. {lic}" if has_voice else
                                  f"Đang tải {name}…" if pack.key in downloading else
                                  f"Chưa tải {name} ({pack.size / 1e6:.0f} MB). {lic}")
        self.btn_voice_dl.setVisible(lib_ok and not has_voice)
        self.btn_voice_dl.setEnabled(pack.key not in downloading)
        self.fit.setEnabled(self.aspect.currentData() != "original")

        has_proj = bool(self.project_dir)
        pdir = Path(self.project_dir) if has_proj else None
        has_wav = has_proj and (pdir / project.VOICE_WAV).is_file() and (pdir / project.VOICE_JSON).is_file()
        self.btn_script.setEnabled(not busy)
        self.btn_voice.setEnabled(not busy and has_proj and (not cloning or bool(self.sample.path())))
        self.btn_edit.setEnabled(not busy and has_wav)
        self.btn_seo.setEnabled(not busy and has_proj)
        self.btn_project.setEnabled(not busy)
        self.btn_open.setEnabled(has_proj)
        self.btn_cancel.setEnabled(busy)

    def set_project(self, path: str | Path, load_text: bool = True) -> None:
        """Chọn dự án; nạp kịch bản (ưu tiên lời đọc đã dùng ở bước 2) và tình trạng các bước."""
        pdir = Path(path)
        data = project.load(pdir)  # InvalidInput nếu không phải dự án
        self.project_dir = str(pdir)
        if load_text:
            text = _voice_text(pdir) or _read(pdir / project.SCRIPT_TXT)
            if text:
                self.text.setPlainText(text)
        if data.get("credit") and not self.credit.text():
            self.credit.setText(data["credit"])
        done = [name for name, ok in (
            ("transcript", (pdir / project.TRANSCRIPT_SRT).is_file()), ("kịch bản", (pdir / project.SCRIPT_TXT).is_file()),
            ("giọng đọc", (pdir / project.VOICE_WAV).is_file()), ("video mới", (pdir / project.VIDEO_MP4).is_file()),
            ("SEO", (pdir / project.SEO_TXT).is_file()),
        ) if ok]
        self.project_label.setText(f"<b>{html.escape(pdir.name)}</b><br>{html.escape(str(pdir.parent))}<br>"
                                   f"Đã có: {html.escape(', '.join(done) or 'chưa có gì')}")
        self._sync()

    def _choose_project(self) -> None:
        start = self.project_dir or self.out.path() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục '… - làm lại'", start)
        if chosen:
            try:
                self.set_project(chosen)
            except PasError as exc:
                show_error(self, exc, "Không mở được dự án")

    # ------------------------------------------------------------ chạy

    def _download(self, pack) -> None:
        answer = QMessageBox.question(
            self, "Tải mô hình",
            f"{pack.title}\n\nDung lượng: {pack.size / 1e6:,.0f} MB\nGiấy phép: {pack.license}\nNguồn: {pack.source}\n"
            + (f"\n{pack.note}\n" if pack.note else "")
            + "\nChỉ tải một lần (cần mạng), kiểm tra mã SHA-256 rồi lưu vào thư mục dữ liệu ứng dụng. "
              "Sau đó chạy hoàn toàn trên máy. Tải ngay?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            job = self.services.jobs.submit(sf_models.DOWNLOAD_KIND, f"Tải {pack.key}", {"model": pack.key})
        except PasError as exc:
            show_error(self, exc, "Không thể tải")
            return
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

    def _run_script(self) -> None:
        path = self.picker.path()
        self._submit(KIND_SCRIPT, f"Làm lại: lấy SUB & viết lại {Path(path).name}", {
            "path": path, "language": self.language.currentData(), "style": self.style.currentData(),
            "output_dir": self.out.path(),
        })

    def _run_voice(self) -> None:
        params = {"project_dir": self.project_dir, "text": self.text.toPlainText(), "voice": self.voice.currentData(),
                  "tone": self.tone.currentData()}
        if params["voice"] == vieneu_tts.KEY:
            if self.preset.currentData() == CLONE:
                params["sample"] = self.sample.path()
            else:
                params["preset"] = self.preset.currentData()
        else:
            params["speaker"] = self.speaker.value()
        self._submit(KIND_VOICE, f"Làm lại: giọng đọc {Path(self.project_dir).name}", params)

    def _run_edit(self) -> None:
        self._submit(KIND_EDIT, f"Làm lại: dựng video {Path(self.project_dir).name}", {
            "project_dir": self.project_dir, "aspect": self.aspect.currentData(), "fit": self.fit.currentData(),
            "zoom": self.zoom.currentData(), "captions": self.captions.isChecked(),
            "blur_subs": self.blur_subs.isChecked(), "bg_volume": self.bg_volume.value() / 100,
            "title": self.title.text().strip(), "credit": self.credit.text().strip(),
        })

    def _run_seo(self) -> None:
        self._submit(KIND_SEO, f"Làm lại: thumbnail + SEO {Path(self.project_dir).name}", {
            "project_dir": self.project_dir, "notes": self.notes.text().strip(), "credit": self.credit.text().strip(),
        })

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
                    # bước 1 tạo dự án mới → nạp kịch bản vào ô bước 2; các bước sau giữ nguyên chữ người dùng đã sửa
                    self.set_project(r["project_dir"], load_text=self.job_kind == KIND_SCRIPT)
                except PasError as exc:
                    self.error.setText(html.escape(exc.message))
                    self.error.show()
                self.result.setHtml(render_result(self.job_kind, r))
        self._sync()

    def refresh(self) -> None:
        self._sync()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _voice_text(pdir: Path) -> str:
    try:
        meta = json.loads((pdir / project.VOICE_JSON).read_text(encoding="utf-8"))
        return "\n\n".join(s["text"] for s in meta.get("segments") or [])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def render_result(kind: str | None, r: dict) -> str:
    e = html.escape
    ok = "<p style='color:#1d5e2e'><b>{}</b></p>"
    if kind == KIND_SCRIPT:
        out = ok.format(f"Bước 1 xong: {r['lines']} câu thoại, dịch được {r['translated']}, "
                        f"kịch bản {r['parts']} đoạn (mô hình {e(r['model'])}).")
        out += f"<p>Chủ đề: {e(r.get('topic') or '')}</p>"
        if r.get("failed"):
            out += ("<p style='color:#a11'><b>Không dịch được:</b> " + e("; ".join(r["failed"][:10]))
                    + ("…" if len(r["failed"]) > 10 else "") + "</p>")
        out += "<p>File: " + e(", ".join(Path(f).name for f in r["files"])) + "</p>"
        out += ("<p style='color:#8a5a00'>Bản dịch và kịch bản do máy viết — đọc lại, sửa ở ô 'Kịch bản' (bước 2) "
                "trước khi tạo giọng đọc.</p>")
        return out
    if kind == KIND_VOICE:
        out = ok.format(f"Bước 2 xong: giọng đọc {format_duration(r['duration'])} ({r['segments']} đoạn, "
                        f"{r['matched']} đoạn khớp kịch bản bước 1).")
        who = (f"VieNeu, nhân bản từ {r['sample']}" if r.get("sample") else
               f"VieNeu {r['preset']}" if r.get("preset") else f"Piper {r['voice']}")
        out += f"<p>File: {e(Path(r['wav']).name)} — giọng {e(who)}, tông {e(tts.TONES[r['tone']].label)}.</p>"
        out += f"<p style='color:#8a5a00'>Giấy phép: {e(r['license'])}. {e(r.get('note') or '')}</p>"
        if r["matched"] < r["segments"]:
            out += ("<p>Những đoạn không khớp kịch bản bước 1 (đã sửa/thêm) sẽ dùng cảnh theo vị trí tương ứng "
                    "trong video gốc.</p>")
        return out
    if kind == KIND_EDIT:
        out = ok.format(f"Bước 3 xong: {e(Path(r['video']).name)} — {r['width']}×{r['height']}, "
                        f"{format_duration(r['duration'])}, {format_size(r['size_bytes'])}, đã qua kiểm tra.")
        out += (f"<p>{r['pieces']} cảnh ngắn (từ {r['source_shots']} cảnh gốc), {r['captions']} dòng phụ đề"
                + (f", làm mờ {r['blurred']} vùng chữ gốc" if r.get("blurred") else "") + ".</p>")
        return out + "<p style='color:#8a5a00'>Xem lại toàn bộ video trước khi đăng.</p>"
    if kind == KIND_SEO:
        out = ok.format(f"Bước 4 xong: {len(r['thumbnails'])} thumbnail + {e(Path(r['seo_file']).name)}.")
        out += "<p><b>Tiêu đề:</b></p><ol>" + "".join(f"<li>{e(t)}</li>" for t in r["titles"]) + "</ol>"
        out += "<p><b>Thumbnail:</b> " + e("; ".join(f"{Path(t['path']).name} ({t['text']})" for t in r["thumbnails"])) + "</p>"
        out += f"<p><b>Từ khóa:</b> {e(', '.join(r['keywords']))}</p><p><b>Hashtag:</b> {e(' '.join(r['hashtags']))}</p>"
        out += f"<p><b>Người xem:</b> {e(r['audience'])}</p>"
        return out + f"<p style='color:#8a5a00'>{e(r['note'])}</p>"
    return ""
