"""Smoke test giao diện (offscreen): khởi động, chuyển trang, chạy kiểm tra media từ UI, lưu cài đặt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from conftest import UNICODE_DIR, make_media
from pas.jobs.models import JobStatus
from pas.services import build_services
from pas.ui.main_window import PLANNED_PAGES, MainWindow


@pytest.fixture
def window(qtbot, data_dir):
    services = build_services(data_dir)
    w = MainWindow(services)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.shutdown()  # giống closeEvent khi không có tác vụ chạy


def test_close_with_pending_events_does_not_touch_closed_db(qtbot, data_dir):
    services = build_services(data_dir)
    w = MainWindow(services)
    qtbot.addWidget(w)
    w._on_job_event(type("E", (), {"job_id": -1})())  # kích hoạt timer làm mới
    w.close()
    qtbot.wait(500)  # timer (300 ms) không được chạy sau khi đóng


def test_all_pages_open(window):
    enabled = [i for i in range(window.nav.count()) if window.nav.item(i).flags() & Qt.ItemFlag.ItemIsEnabled]
    assert len(enabled) == window.stack.count()
    for i in enabled:
        window.nav.setCurrentRow(i)
        assert window.stack.currentIndex() == i


def test_planned_pages_are_disabled(window):
    for i in range(window.stack.count(), window.nav.count()):
        assert not window.nav.item(i).flags() & Qt.ItemFlag.ItemIsEnabled
    assert window.nav.count() - window.stack.count() == len(PLANNED_PAGES)


def test_dashboard_shows_environment(window):
    tree = window.dashboard.env_tree
    names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert "ffmpeg" in names and "Python" in names


@pytest.mark.ffmpeg
def test_media_check_from_ui(window, qtbot, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / UNICODE_DIR / "video giao diện.mp4", seconds=1)
    page = window.media_check
    page.picker.set_path(str(src))
    page.btn_start.click()
    assert page.job_id is not None
    qtbot.waitUntil(lambda: window.services.job_repo.get(page.job_id).status == JobStatus.COMPLETED, timeout=20000)
    qtbot.waitUntil(lambda: "16:9" in page.result.toPlainText(), timeout=5000)
    assert page.btn_start.isEnabled() and not page.btn_cancel.isEnabled()


def test_media_check_empty_path_shows_error(window, monkeypatch):
    shown = []
    monkeypatch.setattr("pas.ui.pages.media_check.show_error", lambda parent, exc, title: shown.append(exc))
    window.media_check.btn_start.click()
    assert shown and shown[0].hint
    assert window.services.job_repo.list() == []


def test_settings_save_persists(window, data_dir):
    page = window.settings
    page.max_jobs.setValue(2)
    page.keep_temp.setChecked(True)
    page._save()
    from pas.core.config import load_settings
    s = load_settings(data_dir / "settings.json")
    assert s.max_concurrent_jobs == 2 and s.keep_temp_files
    assert window.services.jobs.max_workers == 2


def test_projects_page_lists_projects(window):
    window.services.projects.create("Dự án giao diện")
    window.projects.refresh()
    assert window.projects.table.rowCount() == 1
    assert window.projects.table.item(0, 0).text() == "Dự án giao diện"


@pytest.mark.ffmpeg
def test_clips_from_ui(window, qtbot, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / UNICODE_DIR / "clip giao diện.mp4", seconds=6)
    page = window.clips
    page.picker.set_path(str(src))
    page.seconds.setValue(5)
    page.aspect.setCurrentIndex(page.aspect.findData("1:1"))
    assert not page.btn_open.isEnabled()
    page.btn_start.click()
    assert page.job_id is not None
    qtbot.waitUntil(lambda: page.btn_open.isEnabled(), timeout=60000)
    job = window.services.job_repo.get(page.job_id)
    assert job.status == JobStatus.COMPLETED
    assert len(job.result["files"]) == 1  # 6 s: đuôi 1 s được gộp
    assert "Đã tạo 1 clip" in page.result.toPlainText()
    # Lựa chọn được ghi nhớ cho lần sau
    assert (window.services.settings.clip_seconds, window.services.settings.clip_aspect) == (5, "1:1")


def test_clips_invalid_input_shows_error(window, monkeypatch):
    shown = []
    monkeypatch.setattr("pas.ui.pages.clips.show_error", lambda parent, exc, title: shown.append(exc))
    window.clips.btn_start.click()
    assert shown and window.clips.job_id is None


def test_clips_vietsub_options(window, monkeypatch, tmp_path):
    page = window.clips
    page.split.setChecked(False)
    page.vietsub.setChecked(True)
    assert not page.seconds.isEnabled() and page.region.isEnabled()
    page.region.setCurrentIndex(page.region.findData("full"))
    submitted = []
    monkeypatch.setattr(window.services.jobs, "submit",
                        lambda kind, title, params: submitted.append(params) or type("J", (), {"id": 1})())
    page.picker.set_path(str(tmp_path / "v.mp4"))
    page.btn_start.click()
    [p] = submitted
    assert (p["segment_seconds"], p["vietsub"], p["vietsub_region"]) == (0, True, "full")
    s = window.services.settings
    assert (s.clip_split, s.clip_vietsub, s.clip_vietsub_region) == (False, True, "full")


def test_render_result_vietsub():
    from pas.ui.pages.clips import render_result

    base = {"output_dir": "D:/ra", "files": [{"path": "D:/ra/a.mp4", "start": 0, "duration": 5.0, "size_bytes": 10}]}
    none = render_result({**base, "vietsub": {"lines": 0, "translated": 0, "static": 0, "failed": [],
                                              "model": "m", "srt_vi": None, "srt_zh": None}})
    assert "Không tìm thấy phụ đề tiếng Trung" in none
    html = render_result({**base, "vietsub": {"lines": 3, "translated": 2, "static": 1, "failed": ["<坏>"],
                                              "model": "qwen2.5:7b", "srt_vi": "D:/ra/a - vi.srt",
                                              "srt_zh": "D:/ra/a - zh.srt"}})
    assert "dịch 2/3 câu" in html and "a - vi.srt" in html and "&lt;坏&gt;" in html
    assert "vietsub" not in render_result({**base, "vietsub": None})


def test_clips_safety_options(window, monkeypatch, tmp_path):
    page = window.clips
    assert not page.safety_kinds["nudity"].isEnabled()  # tắt kiểm duyệt → các mục con bị khóa
    page.safety.setChecked(True)
    assert all(cb.isEnabled() for cb in page.safety_kinds.values())
    assert not page.btn_model.isHidden() and "chưa tải" in page.model_status.text()  # thư mục dữ liệu mới: chưa có CLIP
    page.safety_kinds["violence"].setChecked(False)
    submitted = []
    monkeypatch.setattr(window.services.jobs, "submit",
                        lambda kind, title, params: submitted.append(params) or type("J", (), {"id": 1})())
    page.picker.set_path(str(tmp_path / "v.mp4"))
    page.btn_start.click()
    [p] = submitted
    assert p["safety"] == {"nudity": True, "violence": False, "text": True}
    s = window.services.settings
    assert (s.clip_safety, s.clip_safety_violence) == (True, False)


def test_clips_download_model_asks_first(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page = window.clips
    page.safety.setChecked(True)
    submitted = []
    monkeypatch.setattr(window.services.jobs, "submit",
                        lambda kind, title, params: submitted.append((kind, params)) or type("J", (), {"id": 7})())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page.btn_model.click()
    assert submitted == []  # người dùng từ chối → không tải
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page.btn_model.click()
    assert submitted == [("model.download", {"model": "clip-vit-b32-vision"})]
    assert page.download_id == 7 and not page.btn_model.isEnabled()


def test_render_result_safety():
    from pas.ui.pages.clips import render_result

    base = {"output_dir": "D:/ra", "files": [], "vietsub": None}
    sf = {"counts": {"nudity": 1, "violence": 0, "text": 0}, "seconds": {}, "enabled": {"nudity": True},
          "report": "D:/ra/a - kiểm duyệt.txt",
          "intervals": [{"kind": "nudity", "start": 1.5, "end": 3.0, "labels": ["<mông lộ>"], "score": 0.9}]}
    html = render_result({**base, "safety": sf})
    assert "đã làm mờ 1 đoạn" in html and "0:01.5–0:03.0" in html and "&lt;mông lộ&gt;" in html
    assert "a - kiểm duyệt.txt" in html and "không bảo đảm" in html
    none = render_result({**base, "safety": {**sf, "intervals": [], "counts": {"nudity": 0}}})
    assert "không phát hiện đoạn nào" in none


def _fake_submit(window, monkeypatch, job_id=1):
    submitted = []
    monkeypatch.setattr(window.services.jobs, "submit",
                        lambda kind, title, params: submitted.append((kind, params)) or type("J", (), {"id": job_id})())
    return submitted


def test_remake_steps_need_project(window, monkeypatch, tmp_path):
    from pas.modules.remake import project

    page = window.remake
    assert page.btn_script.isEnabled()
    assert not (page.btn_voice.isEnabled() or page.btn_edit.isEnabled() or page.btn_seo.isEnabled())
    pdir = tmp_path / UNICODE_DIR / "v - làm lại"
    pdir.mkdir(parents=True)
    project.save(pdir, {"version": 1, "source": str(tmp_path / "v.mp4")})
    (pdir / project.SCRIPT_TXT).write_text("Đoạn một.\n\nĐoạn hai.", encoding="utf-8")
    page.set_project(pdir)
    assert page.text.toPlainText() == "Đoạn một.\n\nĐoạn hai."
    assert page.btn_voice.isEnabled() and page.btn_seo.isEnabled()
    assert not page.btn_edit.isEnabled()  # chưa có giọng đọc
    assert "kịch bản" in page.project_label.text()

    submitted = _fake_submit(window, monkeypatch)
    page.voice.setCurrentIndex(page.voice.findData("vivos"))
    assert page.speaker.isEnabled() and page.speaker.maximum() == 64
    page.speaker.setValue(3)
    page.tone.setCurrentIndex(page.tone.findData("calm"))
    page.btn_voice.click()
    [(kind, p)] = submitted
    assert kind == "remake.voice"
    assert (p["project_dir"], p["voice"], p["speaker"], p["tone"]) == (str(pdir), "vivos", 3, "calm")
    assert p["text"] == "Đoạn một.\n\nĐoạn hai."


def test_remake_open_invalid_project_shows_error(window):
    from pas.core.errors import InvalidInput

    with pytest.raises(InvalidInput):
        window.remake.set_project(window.services.paths.root)
    assert window.remake.project_dir is None


def test_remake_script_params_and_validation(window, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr("pas.ui.pages.remake.show_error", lambda parent, exc, title: shown.append(exc))
    window.remake.btn_script.click()  # chưa chọn video → lỗi từ validate, không tạo tác vụ
    assert shown and window.remake.job_id is None
    submitted = _fake_submit(window, monkeypatch)
    page = window.remake
    page.picker.set_path(str(tmp_path / "v.mp4"))
    page.language.setCurrentIndex(page.language.findData("zh"))
    page.style.setCurrentIndex(page.style.findData("short"))
    page.btn_script.click()
    assert submitted == [("remake.script", {"path": str(tmp_path / "v.mp4"), "language": "zh", "style": "short",
                                            "output_dir": ""})]


def test_remake_download_asks_first(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page = window.remake
    submitted = _fake_submit(window, monkeypatch, job_id=9)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page.btn_whisper.click()
    assert submitted == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page.btn_voice_dl.click()
    assert submitted == [("model.download", {"model": "piper-vi_VN-vais1000-medium"})]
    assert page.downloads == {9: "piper-vi_VN-vais1000-medium"} and not page.btn_voice_dl.isEnabled()


def test_remake_render_results():
    from pas.ui.pages.remake import render_result

    s = render_result("remake.script", {"lines": 10, "translated": 9, "parts": 4, "model": "m", "topic": "<x>",
                                        "failed": ["坏"], "files": ["D:/a/kịch bản.txt"]})
    assert "10 câu thoại" in s and "&lt;x&gt;" in s and "kịch bản.txt" in s and "坏" in s
    v = render_result("remake.voice", {"duration": 65.0, "segments": 5, "matched": 4, "wav": "D:/a/giọng đọc.wav",
                                       "voice": "vais1000", "tone": "natural", "license": "CC BY 4.0", "note": ""})
    assert "4 đoạn khớp" in v and "CC BY 4.0" in v
    e = render_result("remake.edit", {"video": "D:/a/video mới.mp4", "width": 1080, "height": 1920, "duration": 65.0,
                                      "size_bytes": 1000, "pieces": 30, "source_shots": 12, "captions": 20, "blurred": 3})
    assert "1080×1920" in e and "30 cảnh ngắn" in e
    o = render_result("remake.seo", {"thumbnails": [{"path": "D:/a/thumbnail 1.jpg", "text": "SỐC"}],
                                     "seo_file": "D:/a/SEO.txt", "titles": ["T1"], "keywords": ["k"],
                                     "hashtags": ["#h"], "audience": "a", "note": "Không có số liệu"})
    assert "T1" in o and "thumbnail 1.jpg" in o and "Không có số liệu" in o
