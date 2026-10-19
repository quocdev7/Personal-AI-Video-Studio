"""Hàng đợi tác vụ: hoàn thành, lỗi, hủy, tạm dừng, gián đoạn, chạy lại, dọn file tạm."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from pas.core.errors import InvalidInput, PasError
from pas.jobs.manager import JobManager
from pas.jobs.models import JobStatus
from pas.jobs.registry import TaskRegistry, TaskSpec
from pas.jobs.repository import JobRepository


def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("Hết thời gian chờ điều kiện")


class Env:
    def __init__(self, db, tmp_path: Path, keep_temp=False, start=True):
        self.repo = JobRepository(db)
        self.registry = TaskRegistry()
        self.temp = tmp_path / "tạm"
        self.events = []
        self.gate = threading.Event()  # handler "slow" chờ cổng này
        self.started = threading.Event()
        self.registry.register(TaskSpec("ok", "OK", self._ok))
        self.registry.register(TaskSpec("fail", "Fail", self._fail))
        self.registry.register(TaskSpec("bug", "Bug", self._bug))
        self.registry.register(TaskSpec("slow", "Slow", self._slow))
        self.registry.register(TaskSpec("checked", "Checked", self._ok, self._validate))
        self.manager = JobManager(self.repo, self.registry, self.temp, keep_temp=keep_temp)
        self.manager.add_listener(self.events.append)
        if start:
            self.manager.start()

    def _ok(self, ctx):
        (ctx.temp_dir / "trung gian.bin").write_bytes(b"x")
        ctx.report(0.5, "nửa đường", force=True)
        return {"giá trị": "kết quả"}

    def _fail(self, ctx):
        (ctx.temp_dir / "trung gian.bin").write_bytes(b"x")
        raise InvalidInput("File hỏng", "Chọn file khác")

    def _bug(self, ctx):
        raise ZeroDivisionError("chia cho 0")

    def _slow(self, ctx):
        self.started.set()
        while not self.gate.is_set():
            ctx.checkpoint()
            ctx.report(0.3, "đang làm")
            time.sleep(0.02)
        ctx.checkpoint()
        return {}

    @staticmethod
    def _validate(params):
        if not params.get("path"):
            raise InvalidInput("Thiếu đường dẫn")

    def status(self, job_id):
        return self.repo.get(job_id).status

    def wait_status(self, job_id, status):
        wait_for(lambda: self.status(job_id) == status and job_id not in self.manager.running_ids())


@pytest.fixture
def env(db, tmp_path):
    e = Env(db, tmp_path)
    yield e
    e.gate.set()
    e.manager.shutdown(timeout=5)


def test_complete(env: Env):
    job = env.manager.submit("ok", "Tác vụ thử", {"a": "ă"})
    env.wait_status(job.id, JobStatus.COMPLETED)
    j = env.repo.get(job.id)
    assert j.result == {"giá trị": "kết quả"}
    assert j.progress == 1.0 and j.attempts == 1 and j.finished_at
    assert not (env.temp / f"job-{job.id}").exists()  # đã xong → xóa file tạm
    statuses = [e.status for e in env.events if e.job_id == job.id]
    assert statuses[0] == JobStatus.QUEUED and statuses[-1] == JobStatus.COMPLETED
    assert JobStatus.RUNNING in statuses


def test_fail_keeps_error_hint_and_temp(env: Env):
    job = env.manager.submit("fail", "Lỗi", {})
    env.wait_status(job.id, JobStatus.FAILED)
    j = env.repo.get(job.id)
    assert j.error == "File hỏng" and j.error_hint == "Chọn file khác"
    assert (env.temp / f"job-{job.id}" / "trung gian.bin").exists()  # giữ để chẩn đoán
    assert any("File hỏng" in log.message for log in env.repo.logs(job.id))


def test_unexpected_exception_is_reported_not_swallowed(env: Env):
    job = env.manager.submit("bug", "Bug", {})
    env.wait_status(job.id, JobStatus.FAILED)
    j = env.repo.get(job.id)
    assert "ZeroDivisionError" in j.error
    assert any("Traceback" in log.message for log in env.repo.logs(job.id))


def test_validation_rejects_before_queue(env: Env):
    with pytest.raises(InvalidInput):
        env.manager.submit("checked", "x", {})
    assert env.repo.list() == []


def test_unknown_kind(env: Env):
    with pytest.raises(PasError):
        env.manager.submit("không tồn tại", "x", {})


def test_cancel_running(env: Env):
    job = env.manager.submit("slow", "Chậm", {})
    assert env.started.wait(5)
    assert env.manager.cancel(job.id)
    env.wait_status(job.id, JobStatus.CANCELLED)
    assert not (env.temp / f"job-{job.id}").exists()


def test_cancel_queued_does_not_run(env: Env):
    first = env.manager.submit("slow", "Chạy trước", {})
    assert env.started.wait(5)
    second = env.manager.submit("ok", "Chờ", {})
    assert env.status(second.id) == JobStatus.QUEUED  # max_workers=1
    assert env.manager.cancel(second.id)
    assert env.status(second.id) == JobStatus.CANCELLED
    env.gate.set()
    env.wait_status(first.id, JobStatus.COMPLETED)
    assert env.repo.get(second.id).attempts == 0


def test_pause_resume(env: Env):
    job = env.manager.submit("slow", "Chậm", {})
    assert env.started.wait(5)
    assert env.manager.pause(job.id)
    assert env.status(job.id) == JobStatus.PAUSED
    assert not env.manager.pause(job.id)  # đã tạm dừng
    env.gate.set()
    time.sleep(0.3)
    assert env.status(job.id) == JobStatus.PAUSED  # handler đứng ở checkpoint, không hoàn thành
    assert env.manager.resume(job.id)
    env.wait_status(job.id, JobStatus.COMPLETED)


def test_cancel_while_paused(env: Env):
    job = env.manager.submit("slow", "Chậm", {})
    assert env.started.wait(5)
    env.manager.pause(job.id)
    env.manager.cancel(job.id)
    env.wait_status(job.id, JobStatus.CANCELLED)


def test_retry_failed(env: Env):
    job = env.manager.submit("fail", "Lỗi", {})
    env.wait_status(job.id, JobStatus.FAILED)
    assert env.manager.retry(job.id)
    wait_for(lambda: env.repo.get(job.id).attempts == 2 and env.status(job.id) == JobStatus.FAILED)


def test_retry_rejected_for_completed(env: Env):
    job = env.manager.submit("ok", "OK", {})
    env.wait_status(job.id, JobStatus.COMPLETED)
    assert not env.manager.retry(job.id)


def test_jobs_run_in_order(env: Env):
    ids = [env.manager.submit("ok", f"#{i}", {}).id for i in range(5)]
    assert env.manager.wait_idle(10)
    started = [env.repo.get(i).started_at for i in ids]
    assert started == sorted(started)
    assert all(env.status(i) == JobStatus.COMPLETED for i in ids)


def test_shutdown_marks_running_interrupted(db, tmp_path):
    e = Env(db, tmp_path)
    job = e.manager.submit("slow", "Chậm", {})
    assert e.started.wait(5)
    e.manager.shutdown(timeout=5)
    assert e.status(job.id) == JobStatus.INTERRUPTED
    assert (e.temp / f"job-{job.id}").exists()  # giữ file tạm để chạy lại


def test_crash_recovery_on_startup(db, tmp_path):
    """Mô phỏng app bị tắt đột ngột: bản ghi còn RUNNING/PAUSED trong CSDL."""
    repo = JobRepository(db)
    running = repo.create("ok", "Đang chạy khi crash", {})
    paused = repo.create("ok", "Tạm dừng khi crash", {})
    queued = repo.create("ok", "Đang chờ", {})
    repo.update(running.id, status=JobStatus.RUNNING)
    repo.update(paused.id, status=JobStatus.PAUSED)

    e = Env(db, tmp_path, start=False)
    assert sorted(e.manager.interrupted_on_startup) == sorted([running.id, paused.id])
    assert e.status(running.id) == JobStatus.INTERRUPTED
    assert e.status(queued.id) == JobStatus.QUEUED  # tác vụ đang chờ vẫn chạy tiếp
    e.manager.start()
    e.wait_status(queued.id, JobStatus.COMPLETED)
    assert e.status(running.id) == JobStatus.INTERRUPTED  # không tự chạy lại, người dùng quyết định
    assert e.manager.retry(running.id)
    e.wait_status(running.id, JobStatus.COMPLETED)
    e.manager.shutdown()


def test_cleanup_finished_temp(db, tmp_path):
    e = Env(db, tmp_path)
    failed = e.manager.submit("fail", "Lỗi", {})
    e.wait_status(failed.id, JobStatus.FAILED)
    slow = e.manager.submit("slow", "Đang chạy", {})
    assert e.started.wait(5)
    assert e.manager.cleanup_finished_temp() == 1
    assert not (e.temp / f"job-{failed.id}").exists()
    assert (e.temp / f"job-{slow.id}").exists()  # không đụng tác vụ đang chạy
    e.gate.set()
    e.manager.shutdown()


def test_keep_temp_setting(db, tmp_path):
    e = Env(db, tmp_path, keep_temp=True)
    job = e.manager.submit("ok", "OK", {})
    e.wait_status(job.id, JobStatus.COMPLETED)
    assert (e.temp / f"job-{job.id}" / "trung gian.bin").exists()
    e.manager.shutdown()


def test_listener_error_does_not_break_job(db, tmp_path):
    e = Env(db, tmp_path)

    def bad_listener(event):
        raise RuntimeError("listener hỏng")

    e.manager.add_listener(bad_listener)
    job = e.manager.submit("ok", "OK", {})
    e.wait_status(job.id, JobStatus.COMPLETED)
    e.manager.shutdown()
