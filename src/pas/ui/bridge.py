from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from pas.jobs.models import JobEvent


class JobEventBridge(QObject):
    """Nhận JobEvent từ thread worker, phát lại thành Qt signal trên main thread.

    Signal phát từ thread khác được Qt tự chuyển thành queued connection tới slot ở main thread.
    """

    job_changed = Signal(object)  # JobEvent

    def __call__(self, event: JobEvent) -> None:
        self.job_changed.emit(event)
