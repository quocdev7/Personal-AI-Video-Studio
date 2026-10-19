"""Tạo clip ngắn: kế hoạch cắt, bộ lọc, kiểm tra tham số (unit) và chạy thật với FFmpeg qua hàng đợi."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import UNICODE_DIR, make_media
from pas.core.errors import InvalidInput
from pas.core.paths import unique_output_dir
from pas.jobs.models import JobStatus
from pas.media.ffmpeg import probe
from pas.modules.clips.tasks import ASPECTS, FITS, KIND, plan_segments, validate, video_filter
from pas.services import build_services

# ------------------------------------------------------------------ unit


def test_plan_exact_multiple():
    segs = plan_segments(180, 60)
    assert [(s.start, s.duration) for s in segs] == [(0, 60), (60, 60), (120, 60)]
    assert [s.index for s in segs] == [1, 2, 3]


def test_plan_short_tail_is_merged():
    segs = plan_segments(125, 60)  # đuôi 5 s < 30% của 60 s
    assert [(s.start, s.duration) for s in segs] == [(0, 60), (60, 65)]


def test_plan_long_tail_kept():
    segs = plan_segments(140, 60)  # đuôi 20 s ≥ 18 s
    assert [s.duration for s in segs] == [60, 60, 20]


def test_plan_video_shorter_than_segment():
    assert [(s.start, s.duration) for s in plan_segments(10, 60)] == [(0, 10)]


def test_plan_empty():
    assert plan_segments(0, 60) == []


def test_plan_covers_whole_video():
    for total in (61.37, 599.9, 3601.04):
        segs = plan_segments(total, 60)
        assert abs(sum(s.duration for s in segs) - total) < 0.01
        assert all(b.start == pytest.approx(a.start + a.duration) for a, b in zip(segs, segs[1:]))


@pytest.mark.parametrize("aspect", list(ASPECTS))
@pytest.mark.parametrize("fit", list(FITS))
def test_filter_has_single_output(aspect, fit):
    f = video_filter(aspect, fit)
    assert f.startswith("[0:v:0]") and f.endswith("[v]")


@pytest.fixture
def video_file(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    return p


def _params(path, overrides=None, **kw):
    return {"path": str(path), "segment_seconds": 60, "aspect": "9:16", "fit": "blur", "output_dir": "",
            **(overrides or {}), **kw}


@pytest.mark.parametrize("override", [
    {"path": ""},
    {"path": "C:/khong/ton/tai.mp4"},
    {"segment_seconds": 2},
    {"segment_seconds": 4000},
    {"segment_seconds": True},
    {"segment_seconds": "60"},
    {"aspect": "4:5"},
    {"fit": "stretch"},
])
def test_validate_rejects(video_file, override):
    with pytest.raises(InvalidInput):
        validate(_params(video_file, override))


def test_validate_rejects_audio_only(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    with pytest.raises(InvalidInput):
        validate(_params(p))


def test_validate_output_dir_is_file(video_file):
    with pytest.raises(InvalidInput):
        validate(_params(video_file, output_dir=str(video_file)))


def test_validate_ok(video_file):
    validate(_params(video_file))


def test_unique_output_dir_with_dot_in_name(tmp_path):
    d = tmp_path / "phim v1.2 - clip 9x16"
    assert unique_output_dir(d) == d
    d.mkdir()
    assert unique_output_dir(d).name == "phim v1.2 - clip 9x16 (1)"


# ------------------------------------------------------------------ FFmpeg thật

@pytest.fixture
def services(data_dir):
    s = build_services(data_dir)
    yield s
    s.close()


def _run(services, params, timeout=180):
    job = services.jobs.submit(KIND, "test", params)
    assert services.jobs.wait_idle(timeout)
    return services.job_repo.get(job.id)


def _snapshot(p: Path):
    st = p.stat()
    return st.st_size, st.st_mtime_ns


@pytest.mark.ffmpeg
def test_clips_9x16_blur(services, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / UNICODE_DIR / "video gốc & thử.mp4", seconds=12)
    before = _snapshot(src)
    job = _run(services, _params(src, segment_seconds=5))
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)
    r = job.result
    out_dir = Path(r["output_dir"])
    assert out_dir == src.parent / "video gốc & thử - clip 9x16"
    assert [f["index"] for f in r["files"]] == [1, 2, 3]  # 5 + 5 + 2 (2 s ≥ 30% của 5 s)
    for f in r["files"]:
        info = probe(Path(f["path"]), ffmpeg_tools)
        assert (info.video[0].width, info.video[0].height) == (1080, 1920)
        assert info.video[0].codec == "h264" and info.audio[0].codec == "aac"
    assert sum(f["duration"] for f in r["files"]) == pytest.approx(12, abs=0.5)
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "video gốc & thử - phần 1.mp4", "video gốc & thử - phần 2.mp4", "video gốc & thử - phần 3.mp4"]
    assert _snapshot(src) == before  # file gốc không đổi


@pytest.mark.ffmpeg
@pytest.mark.parametrize("aspect,fit,size", [("1:1", "crop", (1080, 1080)), ("16:9", "pad", (1920, 1080))])
def test_clips_other_aspects_without_audio(services, ffmpeg_tools, tmp_path, aspect, fit, size):
    src = make_media(ffmpeg_tools, tmp_path / "dọc không tiếng.mp4", seconds=4, audio=False, size="180x320")
    job = _run(services, _params(src, segment_seconds=5, aspect=aspect, fit=fit))
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)
    info = probe(Path(job.result["files"][0]["path"]), ffmpeg_tools)
    assert (info.video[0].width, info.video[0].height) == size
    assert not info.has_audio


@pytest.mark.ffmpeg
def test_clips_original_aspect(services, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / "gốc.mp4", seconds=3)
    job = _run(services, _params(src, segment_seconds=5, aspect="original"))
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)
    info = probe(Path(job.result["files"][0]["path"]), ffmpeg_tools)
    assert (info.video[0].width, info.video[0].height) == (320, 180)
    assert Path(job.result["output_dir"]).name == "gốc - clip goc"


@pytest.mark.ffmpeg
def test_existing_output_folder_not_touched(services, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / "v.mp4", seconds=2)
    existing = tmp_path / "out" / "v - clip 9x16"
    existing.mkdir(parents=True)
    (existing / "v - phần 1.mp4").write_bytes(b"cua nguoi dung")
    job = _run(services, _params(src, segment_seconds=5, output_dir=str(tmp_path / "out")))
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)
    assert Path(job.result["output_dir"]).name == "v - clip 9x16 (1)"
    assert (existing / "v - phần 1.mp4").read_bytes() == b"cua nguoi dung"


@pytest.mark.ffmpeg
def test_low_disk_space_fails_before_writing(services, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / "v.mp4", seconds=2)
    services.settings.min_free_disk_gb = 1_000_000.0
    job = _run(services, _params(src, segment_seconds=5))
    assert job.status == JobStatus.FAILED
    assert "GB" in job.error and job.error_hint
    assert not (tmp_path / "v - clip 9x16").exists()


@pytest.mark.ffmpeg
def test_cancel_leaves_no_partial_files(services, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / "dài.mp4", seconds=60)
    job = services.jobs.submit(KIND, "test", _params(src, segment_seconds=30))
    deadline = time.monotonic() + 30
    while services.job_repo.get(job.id).status != JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1.0)  # để FFmpeg đang ghi file .part
    services.jobs.cancel(job.id)
    assert services.jobs.wait_idle(30)
    assert services.job_repo.get(job.id).status == JobStatus.CANCELLED
    out_dir = tmp_path / "dài - clip 9x16"
    assert not list(out_dir.glob("*.part"))
    assert not list(out_dir.glob("*.mp4"))


@pytest.mark.ffmpeg
def test_interrupted_job_resumes_without_redoing_clips(data_dir, ffmpeg_tools, tmp_path):
    src = make_media(ffmpeg_tools, tmp_path / "chạy tiếp.mp4", seconds=20)
    services = build_services(data_dir)
    job = services.jobs.submit(KIND, "test", _params(src, segment_seconds=5))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if any("Xong clip 1" in e.message for e in services.job_repo.logs(job.id)):
            break
        time.sleep(0.05)
    services.close()  # như tắt ứng dụng giữa chừng
    services = build_services(data_dir)
    try:
        assert services.job_repo.get(job.id).status == JobStatus.INTERRUPTED
        first = src.parent / "chạy tiếp - clip 9x16" / "chạy tiếp - phần 1.mp4"
        mtime = first.stat().st_mtime_ns
        assert services.jobs.retry(job.id)
        assert services.jobs.wait_idle(180)
        done = services.job_repo.get(job.id)
        assert done.status == JobStatus.COMPLETED, (done.error, done.error_hint)
        assert len(done.result["files"]) == 4
        assert Path(done.result["output_dir"]) == first.parent  # không tạo thư mục "(1)"
        assert first.stat().st_mtime_ns == mtime  # clip 1 không bị mã hóa lại
        assert any("Bỏ qua clip 1" in e.message for e in services.job_repo.logs(job.id))
        assert not list(first.parent.glob("*.part"))
    finally:
        services.close()
