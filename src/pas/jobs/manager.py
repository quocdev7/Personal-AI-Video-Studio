"""JobManager: hàng đợi bền vững (SQLite) + worker thread. Không phụ thuộc Qt."""

from __future__ import annotations

import logging
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from pas.core.db import utcnow
from pas.core.errors import JobCancelled, PasError
from pas.jobs.context import JobContext, JobControl
from pas.jobs.models import FINISHED_STATUSES, Job, JobEvent, JobStatus
from pas.jobs.registry import TaskRegistry
from pas.jobs.repository import JobRepository

log = logging.getLogger(__name__)

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}

Listener = Callable[[JobEvent], None]


class JobManager:
    def __init__(
        self,
        repo: JobRepository,
        registry: TaskRegistry,
        temp_root: Path,
        max_workers: int = 1,
        keep_temp: bool = False,
    ):
        self.repo = repo
        self.registry = registry
        self.temp_root = Path(temp_root)
        self.max_workers = max(1, max_workers)
        self.keep_temp = keep_temp
        self._listeners: list[Listener] = []
        self._controls: dict[int, JobControl] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._cond = threading.Condition()
        self._stopping = False
        self._dispatcher: threading.Thread | None = None
        self.interrupted_on_startup: list[int] = repo.mark_interrupted()
        if self.interrupted_on_startup:
            log.warning("Tác vụ bị gián đoạn từ lần chạy trước: %s", self.interrupted_on_startup)

    # ------------------------------------------------------------ public API

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def start(self) -> None:
        if self._dispatcher is not None:
            return
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="job-dispatcher", daemon=True)
        self._dispatcher.start()

    def submit(self, kind: str, title: str, params: dict, project_id: int | None = None) -> Job:
        spec = self.registry.get(kind)
        if spec.validate:
            spec.validate(params)
        job = self.repo.create(kind, title, params, project_id)
        self.repo.add_log(job.id, "INFO", f"Đã thêm vào hàng đợi: {title}")
        self._emit(job.id, JobStatus.QUEUED, 0.0, "")
        self._wake()
        return job

    def cancel(self, job_id: int) -> bool:
        if self.repo.cancel_if_queued(job_id):
            self.repo.add_log(job_id, "INFO", "Người dùng hủy khi tác vụ đang chờ")
            self._emit(job_id, JobStatus.CANCELLED, 0.0, "Đã hủy trước khi chạy")
            return True
        with self._cond:
            control = self._controls.get(job_id)
        if control is None:
            return False
        self.repo.add_log(job_id, "INFO", "Người dùng yêu cầu hủy")
        control.cancel()
        return True

    def pause(self, job_id: int) -> bool:
        with self._cond:
            control = self._controls.get(job_id)
        if control is None or control.is_paused:
            return False
        if not self.repo.transition(job_id, JobStatus.RUNNING, JobStatus.PAUSED):
            return False
        control.pause()
        job = self.repo.get(job_id)
        self.repo.add_log(job_id, "INFO", "Người dùng tạm dừng")
        self._emit(job_id, JobStatus.PAUSED, job.progress, job.message)
        return True

    def resume(self, job_id: int) -> bool:
        with self._cond:
            control = self._controls.get(job_id)
        if control is None or not control.is_paused:
            return False
        self.repo.transition(job_id, JobStatus.PAUSED, JobStatus.RUNNING)
        control.resume()
        job = self.repo.get(job_id)
        self.repo.add_log(job_id, "INFO", "Người dùng cho chạy tiếp")
        self._emit(job_id, JobStatus.RUNNING, job.progress, job.message)
        return True

    def retry(self, job_id: int) -> bool:
        if not self.repo.requeue(job_id):
            return False
        self.repo.add_log(job_id, "INFO", "Đưa lại vào hàng đợi")
        self._emit(job_id, JobStatus.QUEUED, 0.0, "")
        self._wake()
        return True

    def running_ids(self) -> list[int]:
        with self._cond:
            return list(self._controls)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Dừng dispatcher; tác vụ đang chạy bị dừng và đánh dấu INTERRUPTED để chạy lại sau."""
        with self._cond:
            self._stopping = True
            controls = list(self._controls.values())
            threads = list(self._threads.values())
            self._cond.notify_all()
        for c in controls:
            c.cancel()
        for t in threads:
            t.join(timeout=timeout)
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=timeout)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Chờ tới khi không còn tác vụ chờ/chạy (dùng trong test và CLI)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                busy = bool(self._threads)
            if not busy and not self.repo.list({JobStatus.QUEUED}, limit=1):
                return True
            time.sleep(0.05)
        return False

    def cleanup_finished_temp(self) -> int:
        """Xóa thư mục tạm của các tác vụ đã kết thúc. Trả về số thư mục đã xóa."""
        if not self.temp_root.exists():
            return 0
        finished = {j.id for j in self.repo.list(FINISHED_STATUSES, limit=100000)}
        removed = 0
        for d in self.temp_root.glob("job-*"):
            try:
                job_id = int(d.name.removeprefix("job-"))
            except ValueError:
                continue
            if job_id in finished and d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    # ------------------------------------------------------------ internals

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _emit(self, job_id: int, status: JobStatus, progress: float, message: str) -> None:
        event = JobEvent(job_id, status, progress, message)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                log.exception("Listener lỗi khi nhận sự kiện tác vụ %s", job_id)

    def _dispatch_loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopping and len(self._threads) >= self.max_workers:
                    self._cond.wait(timeout=1.0)
                if self._stopping:
                    return
                # Nhận tác vụ và đăng ký control trong cùng một khóa: không có khoảnh khắc nào tác vụ đã
                # RUNNING trong CSDL mà cancel()/running_ids()/wait_idle() lại không thấy.
                job = self.repo.claim_next()
                if job is None:
                    self._cond.wait(timeout=1.0)
                    continue
                control = JobControl()
                thread = threading.Thread(target=self._run_job, args=(job, control), name=f"job-{job.id}",
                                          daemon=True)
                self._controls[job.id] = control
                self._threads[job.id] = thread
                thread.start()  # trong khóa: shutdown() không bao giờ join() một thread chưa start

    def _run_job(self, job: Job, control: JobControl) -> None:
        temp_dir = self.temp_root / f"job-{job.id}"
        status = JobStatus.FAILED
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            self.repo.add_log(job.id, "INFO", f"Bắt đầu (lần {job.attempts})")
            self._emit(job.id, JobStatus.RUNNING, 0.0, "")

            def on_progress(progress: float, message: str) -> None:
                current = JobStatus.PAUSED if control.is_paused else JobStatus.RUNNING
                self.repo.update(job.id, progress=progress, message=message)
                self._emit(job.id, current, progress, message)

            def on_log(level: str, message: str) -> None:
                self.repo.add_log(job.id, level, message)
                log.log(_LEVELS.get(level, logging.INFO), "[job %s] %s", job.id, message)

            ctx = JobContext(job.id, job.params, temp_dir, control, on_progress, on_log)
            spec = self.registry.get(job.kind)
            result = spec.handler(ctx)
            if control.is_cancelled:
                raise JobCancelled()
            status = JobStatus.COMPLETED
            self.repo.update(job.id, status=status, progress=1.0, message="Hoàn thành",
                             result=result, finished_at=utcnow())
            self.repo.add_log(job.id, "INFO", "Hoàn thành")
            self._emit(job.id, status, 1.0, "Hoàn thành")
        except JobCancelled:
            with self._cond:
                stopping = self._stopping
            status = JobStatus.INTERRUPTED if stopping else JobStatus.CANCELLED
            msg = "Dừng do đóng ứng dụng" if stopping else "Đã hủy"
            current = self.repo.get(job.id)
            self.repo.update(job.id, status=status, message=msg, finished_at=utcnow())
            self.repo.add_log(job.id, "INFO", msg)
            self._emit(job.id, status, current.progress if current else 0.0, msg)
        except PasError as exc:
            status = JobStatus.FAILED
            self.repo.update(job.id, status=status, message="Lỗi", error=exc.message,
                             error_hint=exc.hint, finished_at=utcnow())
            self.repo.add_log(job.id, "ERROR", str(exc))
            log.warning("Tác vụ %s lỗi: %s", job.id, exc)
            self._emit(job.id, status, 0.0, exc.message)
        except Exception as exc:  # lỗi lập trình: ghi đầy đủ traceback, không nuốt
            status = JobStatus.FAILED
            message = f"Lỗi không mong đợi: {type(exc).__name__}: {exc}"
            self.repo.update(job.id, status=status, message="Lỗi", error=message,
                             error_hint="Đây là lỗi của ứng dụng. Gửi file log (Cài đặt → Mở thư mục log) để sửa.",
                             finished_at=utcnow())
            self.repo.add_log(job.id, "ERROR", message + "\n" + traceback.format_exc())
            log.exception("Tác vụ %s lỗi không mong đợi", job.id)
            self._emit(job.id, status, 0.0, message)
        finally:
            # Giữ file tạm khi lỗi/gián đoạn để chẩn đoán hoặc chạy tiếp.
            if status in (JobStatus.COMPLETED, JobStatus.CANCELLED) and not self.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)
            with self._cond:
                self._controls.pop(job.id, None)
                self._threads.pop(job.id, None)
                self._cond.notify_all()
