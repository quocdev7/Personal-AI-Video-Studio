"""Điểm khởi động giao diện."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from pas import APP_NAME, __version__
from pas.core.errors import PasError
from pas.core.logging_setup import setup_logging
from pas.core.paths import AppPaths, default_data_dir
from pas.services import build_services
from pas.ui.theme import STYLESHEET

log = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyleSheet(STYLESHEET)

    paths = AppPaths(default_data_dir()).ensure()
    log_file = setup_logging(paths.logs_dir)
    log.info("Khởi động %s %s", APP_NAME, __version__)

    # Hai cửa sổ cùng ghi một CSDL và cùng chạy hàng đợi sẽ gây tranh chấp → chỉ cho một phiên.
    lock = QLockFile(str(paths.root / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        log.warning("Đã có phiên khác đang chạy (khóa %s) — thoát", lock.fileName())
        QMessageBox.warning(None, APP_NAME, "Ứng dụng đang chạy ở một cửa sổ khác.")
        return 1

    try:
        services = build_services(paths.root)
    except PasError as exc:
        log.error("Không khởi động được: %s", exc)
        QMessageBox.critical(None, APP_NAME, f"{exc.message}\n\n{exc.hint or ''}\n\nLog: {log_file}")
        return 1

    from pas.ui.main_window import MainWindow

    window = MainWindow(services)
    window.show()
    code = app.exec()
    lock.unlock()
    return code
