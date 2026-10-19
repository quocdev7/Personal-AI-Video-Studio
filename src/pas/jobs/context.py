"""JobContext: thứ duy nhất handler của module cần để báo tiến trình, ghi log và hỗ trợ hủy/tạm dừng."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

from pas.core.errors import JobCancelled

log = logging.getLogger(__name__)


class JobControl:
    """Cờ điều khiển dùng chung giữa JobManager (main thread) và worker."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._running = threading.Event()  # set = không tạm dừng
        self._running.set()
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen] = []

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def is_paused(self) -> bool:
        return not self._running.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        self._running.set()  # đánh thức worker đang chờ ở checkpoint
        with self._lock:
            procs = list(self._processes)
        for p in procs:
            _resume_process(p)
            try:
                p.kill()
            except OSError:
                pass

    def pause(self) -> None:
        self._running.clear()
        with self._lock:
            procs = list(self._processes)
        for p in procs:
            _suspend_process(p)

    def resume(self) -> None:
        with self._lock:
            procs = list(self._processes)
        for p in procs:
            _resume_process(p)
        self._running.set()

    def attach_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._processes.append(proc)
        if self.is_paused:
            _suspend_process(proc)
        if self.is_cancelled:
            proc.kill()

    def detach_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc in self._processes:
                self._processes.remove(proc)

    def wait_while_paused(self) -> None:
        while not self._running.wait(timeout=0.5):
            pass


def _suspend_process(proc: subprocess.Popen) -> None:
    try:
        psutil.Process(proc.pid).suspend()
    except (psutil.Error, OSError):
        log.debug("Không tạm dừng được tiến trình %s", proc.pid, exc_info=True)


def _resume_process(proc: subprocess.Popen) -> None:
    try:
        psutil.Process(proc.pid).resume()
    except (psutil.Error, OSError):
        log.debug("Không tiếp tục được tiến trình %s", proc.pid, exc_info=True)


class JobContext:
    PERSIST_INTERVAL = 0.5  # giây; tránh ghi SQLite mỗi khung hình

    def __init__(
        self,
        job_id: int,
        params: dict,
        temp_dir: Path,
        control: JobControl,
        on_progress: Callable[[float, str], None],
        on_log: Callable[[str, str], None],
    ):
        self.job_id = job_id
        self.params = params
        self.temp_dir = temp_dir
        self._control = control
        self._on_progress = on_progress
        self._on_log = on_log
        self._progress = 0.0
        self._message = ""
        self._last_emit = 0.0

    @property
    def is_cancelled(self) -> bool:
        return self._control.is_cancelled

    @property
    def progress(self) -> float:
        return self._progress

    def checkpoint(self) -> None:
        """Gọi giữa các bước: dừng tại đây nếu đang tạm dừng, ném JobCancelled nếu bị hủy."""
        if self._control.is_cancelled:
            raise JobCancelled()
        if self._control.is_paused:
            self.log("Tạm dừng")
            self._control.wait_while_paused()
            if self._control.is_cancelled:
                raise JobCancelled()
            self.log("Tiếp tục")

    def report(self, progress: float | None = None, message: str | None = None, force: bool = False) -> None:
        if progress is not None:
            self._progress = max(0.0, min(1.0, progress))
        if message is not None and message != self._message:
            self._message = message
            force = True
        now = time.monotonic()
        if force or now - self._last_emit >= self.PERSIST_INTERVAL:
            self._last_emit = now
            self._on_progress(self._progress, self._message)

    def sub_progress(self, start: float, end: float) -> Callable[[float], None]:
        """Ánh xạ tiến trình 0..1 của một bước con vào khoảng [start, end] của tác vụ."""
        span = end - start
        return lambda p: self.report(start + span * max(0.0, min(1.0, p)))

    def log(self, message: str, level: str = "INFO") -> None:
        self._on_log(level, message)

    def attach_process(self, proc: subprocess.Popen) -> None:
        self._control.attach_process(proc)

    def detach_process(self, proc: subprocess.Popen) -> None:
        self._control.detach_process(proc)
