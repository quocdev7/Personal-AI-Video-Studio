"""Log xoay vòng ra file, UTF-8, và bắt exception chưa được xử lý."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)

    if sys.stderr is not None:  # Bản đóng gói dạng windowed không có stderr.
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("pas.uncaught").critical("Exception chưa xử lý", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args: threading.ExceptHookArgs):
        logging.getLogger("pas.uncaught").critical(
            "Exception chưa xử lý trong thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return log_file
