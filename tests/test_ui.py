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
