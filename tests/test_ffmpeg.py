"""FFmpeg thật: đường dẫn Unicode, file hỏng/thiếu, hủy giữa chừng, kiểm tra media qua hàng đợi."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from conftest import UNICODE_DIR, make_media
from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput, JobCancelled
from pas.jobs.context import JobContext, JobControl
from pas.jobs.manager import JobManager
from pas.jobs.models import JobStatus
from pas.jobs.registry import TaskRegistry
from pas.jobs.repository import JobRepository
from pas.media.ffmpeg import parse_probe_json, probe, resolve_tools, run_ffmpeg
from pas.modules.media_check import tasks as media_check

pytestmark = pytest.mark.ffmpeg


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory, ffmpeg_tools) -> Path:
    d = tmp_path_factory.mktemp("media") / UNICODE_DIR
    make_media(ffmpeg_tools, d / "Video thử — tập 1.mp4", seconds=2)
    make_media(ffmpeg_tools, d / "dọc 9x16.mp4", seconds=1, size="180x320")
    make_media(ffmpeg_tools, d / "không tiếng.mp4", seconds=1, audio=False)
    make_media(ffmpeg_tools, d / "âm thanh.m4a", seconds=1, video=False)
    make_media(ffmpeg_tools, d / "im lặng.mp4", seconds=2, silent=True)
    (d / "hỏng.mp4").write_bytes(b"\x00" * 4096)
    (d / "rỗng.mp4").write_bytes(b"")
    return d


def test_resolve_tools_bad_configured_path():
    with pytest.raises(DependencyMissing) as ei:
        resolve_tools(r"C:\không tồn tại\ffmpeg.exe", r"C:\không tồn tại\ffprobe.exe")
    assert "winget" in ei.value.hint


def test_probe_unicode_path(media_dir, ffmpeg_tools):
    info = probe(media_dir / "Video thử — tập 1.mp4", ffmpeg_tools)
    assert 1.9 < info.duration < 2.2
    assert info.video[0].width == 320 and info.video[0].height == 180
    assert info.video[0].fps == 25
    assert info.audio and info.audio[0].sample_rate == 44100


def test_probe_missing_file(media_dir, ffmpeg_tools):
    with pytest.raises(InvalidInput):
        probe(media_dir / "không có.mp4", ffmpeg_tools)


def test_probe_directory(media_dir, ffmpeg_tools):
    with pytest.raises(InvalidInput):
        probe(media_dir, ffmpeg_tools)


@pytest.mark.parametrize("name", ["hỏng.mp4", "rỗng.mp4"])
def test_probe_corrupt(media_dir, ffmpeg_tools, name):
    with pytest.raises(InvalidInput) as ei:
        probe(media_dir / name, ffmpeg_tools)
    assert ei.value.hint


def test_parse_probe_skips_cover_art_and_uses_stream_duration():
    data = {
        "format": {"format_name": "mp3"},
        "streams": [
            {"index": 0, "codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2,
             "duration": "12.5"},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ],
    }
    info = parse_probe_json("x.mp3", data)
    assert info.video == [] and info.duration == 12.5


def test_run_ffmpeg_progress_and_output(media_dir, ffmpeg_tools, tmp_path):
    out = tmp_path / UNICODE_DIR / "kết quả.wav"
    out.parent.mkdir()
    progress = []
    run_ffmpeg(ffmpeg_tools, ["-i", str(media_dir / "Video thử — tập 1.mp4"), "-vn", str(out)],
               duration=2.0, on_progress=progress.append)
    assert out.stat().st_size > 0
    assert progress and progress[-1] > 0.9


def test_run_ffmpeg_error_has_stderr(ffmpeg_tools, media_dir, tmp_path):
    with pytest.raises(ExternalToolError) as ei:
        run_ffmpeg(ffmpeg_tools, ["-i", str(media_dir / "hỏng.mp4"), str(tmp_path / "x.mp4")])
    assert "mã" in ei.value.message


def test_run_ffmpeg_cancel_kills_process(ffmpeg_tools, tmp_path):
    control = JobControl()
    ctx = JobContext(1, {}, tmp_path, control, lambda p, m: None, lambda lv, m: None)
    # Nguồn vô hạn, chỉ dừng khi bị hủy.
    args = ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "null", "-"]
    errors = []

    def worker():
        try:
            run_ffmpeg(ffmpeg_tools, args, ctx=ctx)
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 10
    while not control._processes and time.monotonic() < deadline:
        time.sleep(0.05)
    proc = control._processes[0]
    time.sleep(0.5)
    control.cancel()
    t.join(10)
    assert not t.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], JobCancelled)
    assert proc.poll() is not None


def test_run_ffmpeg_pause_suspends_process(ffmpeg_tools, tmp_path):
    import psutil

    control = JobControl()
    ctx = JobContext(1, {}, tmp_path, control, lambda p, m: None, lambda lv, m: None)
    args = ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "null", "-"]
    t = threading.Thread(target=lambda: _swallow(run_ffmpeg, ffmpeg_tools, args, ctx=ctx))
    t.start()
    deadline = time.monotonic() + 10
    while not control._processes and time.monotonic() < deadline:
        time.sleep(0.05)
    proc = psutil.Process(control._processes[0].pid)
    control.pause()
    time.sleep(0.3)
    cpu_paused = proc.cpu_times()
    time.sleep(0.7)
    assert proc.cpu_times().user - cpu_paused.user < 0.05  # không tiêu thụ CPU khi tạm dừng
    control.resume()
    control.cancel()
    t.join(10)
    assert not t.is_alive()


def _swallow(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except JobCancelled:
        pass


# ------------------------------------------------------------------ media.inspect qua hàng đợi

@pytest.fixture
def inspect_env(db, tmp_path, ffmpeg_tools):
    repo = JobRepository(db)
    registry = TaskRegistry()
    media_check.register(registry, lambda: ffmpeg_tools)
    manager = JobManager(repo, registry, tmp_path / "tạm")
    manager.start()
    yield repo, manager
    manager.shutdown()


def _inspect(inspect_env, path: Path):
    repo, manager = inspect_env
    job = manager.submit(media_check.KIND, path.name, {"path": str(path)})
    assert manager.wait_idle(30)
    return repo.get(job.id)


def test_inspect_ok(inspect_env, media_dir):
    src = media_dir / "Video thử — tập 1.mp4"
    before = (src.stat().st_size, src.stat().st_mtime_ns)
    j = _inspect(inspect_env, src)
    assert j.status == JobStatus.COMPLETED, j.error
    assert j.result["aspect"] == "16:9" and j.result["issues"] == []
    assert (src.stat().st_size, src.stat().st_mtime_ns) == before  # file gốc không đổi


def test_inspect_vertical(inspect_env, media_dir):
    assert _inspect(inspect_env, media_dir / "dọc 9x16.mp4").result["aspect"] == "9:16"


def test_inspect_no_audio_warns(inspect_env, media_dir):
    j = _inspect(inspect_env, media_dir / "không tiếng.mp4")
    assert j.status == JobStatus.COMPLETED
    assert any("âm thanh" in i for i in j.result["issues"])


def test_inspect_audio_only(inspect_env, media_dir):
    j = _inspect(inspect_env, media_dir / "âm thanh.m4a")
    assert j.status == JobStatus.COMPLETED and j.result["video"] == [] and j.result["aspect"] == ""


def test_inspect_silent_video(inspect_env, media_dir):
    j = _inspect(inspect_env, media_dir / "im lặng.mp4")
    assert j.status == JobStatus.COMPLETED and j.result["audio"]


def test_inspect_corrupt_fails_with_hint(inspect_env, media_dir):
    j = _inspect(inspect_env, media_dir / "hỏng.mp4")
    assert j.status == JobStatus.FAILED and j.error_hint


def test_inspect_validation(inspect_env, media_dir, tmp_path):
    _, manager = inspect_env
    with pytest.raises(InvalidInput):
        manager.submit(media_check.KIND, "x", {"path": ""})
    with pytest.raises(InvalidInput):
        manager.submit(media_check.KIND, "x", {"path": str(media_dir / "không có.mp4")})
    txt = tmp_path / "ghi chú.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(InvalidInput):
        manager.submit(media_check.KIND, "x", {"path": str(txt)})


def test_inspect_missing_ffmpeg_fails_with_install_hint(db, tmp_path, media_dir):
    repo = JobRepository(db)
    registry = TaskRegistry()
    media_check.register(registry, lambda: resolve_tools(r"C:\không có\ffmpeg.exe", r"C:\không có\ffprobe.exe"))
    manager = JobManager(repo, registry, tmp_path / "tạm")
    manager.start()
    try:
        job = manager.submit(media_check.KIND, "x", {"path": str(media_dir / "Video thử — tập 1.mp4")})
        assert manager.wait_idle(10)
        j = repo.get(job.id)
        assert j.status == JobStatus.FAILED and "winget" in j.error_hint
    finally:
        manager.shutdown()
