"""Việt hóa phụ đề: gom sự kiện OCR, dịch qua Ollama (máy chủ giả), dựng bộ lọc/ASS/SRT (unit)
và chạy thật OCR + FFmpeg trên video có phụ đề Trung vẽ sẵn bằng drawtext."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import UNICODE_DIR
from pas.core.config import Settings
from pas.core.errors import DependencyMissing, InvalidInput
from pas.jobs.models import JobStatus
from pas.media.ffmpeg import CREATE_NO_WINDOW, probe
from pas.modules.clips.tasks import KIND, folder_name, plan_segments, validate, vietsub_filter
from pas.modules.vietsub import ocr as vs_ocr
from pas.modules.vietsub import render as vs_render
from pas.modules.vietsub.ocr import Line, SubEvent, group_events, has_cjk, sample_geometry
from pas.modules.vietsub.translate import OllamaClient, build_prompt, parse_items, translate_lines
from pas.services import build_services

# ------------------------------------------------------------------ OCR → sự kiện


def _line(text, y0=300.0, x0=100.0, x1=500.0, h=30.0, score=0.95):
    return Line(text, score, (x0, y0, x1, y0 + h))


def test_has_cjk():
    assert has_cjk("你好") and has_cjk("Anh ta明天")
    assert not has_cjk("Xin chào, đây là tiếng Việt 123")


def test_group_single_line_times():
    frames = [(i, [_line("今天天气很好")]) for i in range(2, 10)]
    [e] = group_events(frames, fps=4)
    assert (e.start, e.end) == (0.5, 2.5)
    assert e.text == "今天天气很好" and not e.static
    assert e.line_height == 30.0


def test_group_tolerates_one_missed_frame_and_ocr_noise():
    frames = [(0, [_line("我们去公园吧")]), (1, [_line("我们去公园吗", score=0.7)]),
              (3, [_line("我们去公园吧")]), (4, [_line("我们去公园吧")])]
    [e] = group_events(frames, fps=4)
    assert e.text == "我们去公园吧"  # đọc nhiều lần nhất thắng
    assert (e.start, e.end) == (0.0, 1.25)


def test_group_splits_on_long_gap_and_text_change():
    frames = [(0, [_line("第一句")]), (1, [_line("第一句")]),
              (5, [_line("第一句")]), (6, [_line("第一句")]),
              (7, [_line("完全不同的话")]), (8, [_line("完全不同的话")])]
    events = group_events(frames, fps=4)
    assert [e.text for e in events] == ["第一句", "第一句", "完全不同的话"]


def test_group_drops_single_frame_flicker():
    assert group_events([(0, [_line("闪")]), (5, [_line("烁")])], fps=4) == []


def test_group_merges_two_line_subtitle():
    frames = [(i, [_line("第一行字幕", y0=300), _line("第二行字幕", y0=335)]) for i in range(4)]
    [e] = group_events(frames, fps=4)
    assert e.text == "第一行字幕 第二行字幕"
    assert e.box[1] == 300 and e.box[3] == 365


def test_group_keeps_separate_regions_apart():
    frames = [(i, [_line("顶部标题", y0=20), _line("底部字幕", y0=300)]) for i in range(4)]
    assert len(group_events(frames, fps=4)) == 2


def test_static_text_is_watermark():
    frames = [(i, [_line("水印台标", y0=10, x0=10, x1=120)]) for i in range(0, 4 * 25)]
    [e] = group_events(frames, fps=4)
    assert e.static


def test_subevent_roundtrip():
    e = SubEvent(1.0, 2.0, "你好", (1.0, 2.0, 3.0, 4.0), 2.0, False, "xin chào")
    assert SubEvent.from_dict(json.loads(json.dumps(e.to_dict()))) == e


def test_sample_geometry():
    g = sample_geometry(1920, 1080, "bottom")
    assert (g.y0, g.height, g.out_w, g.out_h) == (540, 540, 960, 270)
    assert g.to_source(480, 100) == (960, 740)
    g = sample_geometry(321, 181, "full")
    assert g.y0 == 0 and g.height % 2 == 0 and g.out_w % 2 == 0 and g.out_h % 2 == 0


# ------------------------------------------------------------------ dịch


def test_parse_items_filters_bad_output():
    data = {"items": [
        {"id": 0, "vi": "  Xin   chào "}, {"id": 1, "vi": "Anh ta明天"}, {"id": 2, "vi": ""},
        {"id": True, "vi": "bool"}, {"id": "3", "vi": "chuỗi"}, {"id": 9, "vi": "lạ"}, "rác", {"id": 4},
    ]}
    assert parse_items(data, {0, 1, 2, 3, 4}) == {0: "Xin chào"}
    assert parse_items({}, {0}) == {} and parse_items([], {0}) == {}  # type: ignore[arg-type]


def test_build_prompt():
    assert build_prompt({3: "你好", 4: "再见"}) == "3: 你好\n4: 再见"


class FakeClient:
    """Lô đầu bỏ sót / trả chữ Hán cho vài câu để kiểm tra dịch lại từng câu."""

    model = "fake"

    def __init__(self, bad: set[str]):
        self.bad = bad
        self.calls: list[dict[int, str]] = []

    def chat_json(self, user, num_predict, temperature=0.2, ctx=None):
        lines = dict((int(a), b) for a, b in (ln.split(": ", 1) for ln in user.splitlines()))
        self.calls.append(lines)
        items = []
        for i, t in lines.items():
            if t in self.bad:
                items.append({"id": i, "vi": t})  # còn chữ Hán → bị loại
            elif len(lines) > 1 and t == "漏":
                continue  # sót trong lô, dịch riêng thì được
            else:
                items.append({"id": i, "vi": f"Câu {i}"})
        return {"items": items}


def test_translate_lines_retry_and_failed():
    client = FakeClient(bad={"坏"})
    lines = {i: t for i, t in enumerate(["好", "漏", "坏"] + ["行"] * 7)}
    saved: list[dict] = []
    progress: list[float] = []
    tr = translate_lines(client, lines, on_batch=saved.append, on_progress=progress.append)
    assert tr.failed == [2]
    assert tr.vi == {i: f"Câu {i}" for i in lines if i != 2}
    assert len(saved) == 2 and progress == [0.8, 1.0]  # lô 8 + lô 2
    singles = [c for c in client.calls if len(c) == 1]
    assert singles == [{1: "漏"}, {2: "坏"}]


def test_translate_lines_skips_done():
    client = FakeClient(bad=set())
    tr = translate_lines(client, {0: "一", 1: "二"}, done={0: "Đã có"})
    assert tr.vi == {0: "Đã có", 1: "Câu 1"}
    assert client.calls == [{1: "二"}]


# ------------------------------------------------------------------ Ollama giả (HTTP thật trên 127.0.0.1)


class _FakeOllama(BaseHTTPRequestHandler):
    models = ["qwen2.5:7b"]
    chat_requests: list[dict] = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": m, "model": m} for m in self.models]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).chat_requests.append(body)
        user = body["messages"][-1]["content"]
        items = [{"id": int(ln.split(":", 1)[0]), "vi": f"Phụ đề Việt số {ln.split(':', 1)[0]}"}
                 for ln in user.splitlines()]
        self._send({"message": {"role": "assistant", "content": json.dumps({"items": items}, ensure_ascii=False)},
                    "done": True})


@pytest.fixture
def fake_ollama():
    _FakeOllama.models = ["qwen2.5:7b"]
    _FakeOllama.chat_requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_ollama_ready_and_chat(fake_ollama):
    client = OllamaClient(fake_ollama, "qwen2.5:7b")
    client.ensure_ready()
    tr = translate_lines(client, {0: "你好", 1: "再见"})
    assert tr.vi == {0: "Phụ đề Việt số 0", 1: "Phụ đề Việt số 1"} and not tr.failed
    req = _FakeOllama.chat_requests[0]
    assert req["stream"] is False and req["format"]["required"] == ["items"]
    assert req["options"]["num_predict"] > 0  # luôn giới hạn token


def test_ollama_missing_model(fake_ollama):
    with pytest.raises(DependencyMissing) as exc:
        OllamaClient(fake_ollama, "qwen2.5:14b").ensure_ready()
    assert "ollama pull qwen2.5:14b" in exc.value.hint


def test_ollama_unreachable():
    import socket

    with socket.socket() as s:  # cổng trống: chắc chắn không có ai nghe
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(DependencyMissing) as exc:
        OllamaClient(f"http://127.0.0.1:{port}").ensure_ready()
    assert "winget install Ollama.Ollama" in exc.value.hint


# ------------------------------------------------------------------ dựng bộ lọc / phụ đề


def _ev(start, end, y0=300.0, text="你好", vi="Xin chào", static=False, x0=100.0, x1=500.0):
    return SubEvent(start, end, text, (x0, y0, x1, y0 + 30.0), 30.0, static, vi)


def test_local_events_clip_to_segment():
    events = [_ev(1, 3), _ev(9, 12), _ev(20, 21)]
    items = vs_render.local_events(events, 10, 10)
    assert [(s, t) for _, s, t in items] == [(0.0, 2.0)]
    assert [(s, t) for _, s, t in vs_render.local_events(events, 0, 10)] == [(1.0, 3.0), (9.0, 10.0)]


def test_blur_groups_merge_same_row_and_even_boxes():
    items = [(_ev(0, 1), 0.0, 1.0), (_ev(1, 2, x0=150, x1=450), 1.0, 2.0), (_ev(3, 4, y0=20), 3.0, 4.0)]
    groups = vs_render.blur_groups(items, 640, 360)
    assert len(groups) == 2
    bottom = next(g for g in groups if g.box[1] > 100)
    assert bottom.windows == [(0.0, 2.0)]  # 2 câu liền nhau cùng dòng → 1 cửa sổ
    for g in groups:
        x, y, w, h = g.box
        assert all(v % 2 == 0 for v in g.box)
        assert x >= 0 and y >= 0 and x + w <= 640 and y + h <= 360


def test_blur_groups_capped():
    items = [(_ev(i, i + 1, y0=5 + i * 14, x0=0, x1=40 + i), float(i), i + 1.0) for i in range(24)]
    items += [(_ev(30 + i, 31 + i, y0=5 + i * 14, x0=300, x1=340), 30.0 + i, 31.0 + i) for i in range(24)]
    assert len(vs_render.blur_groups(items, 640, 360)) <= vs_render.MAX_BLUR_GROUPS


def test_blur_graph_shape():
    g = [vs_render.BlurGroup((10, 20, 100, 40), [(0.0, 1.5), (3.0, 4.0)])]
    s = vs_render.blur_graph(g, "[0:v:0]", "[out]")
    assert s.startswith("[0:v:0]split=2[vs0][vs1];[vs1]crop=100:40:10:20,boxblur=")
    assert s.endswith("overlay=10:20:enable='between(t,0.000,1.500)+between(t,3.000,4.000)'[out]")


def test_vietsub_filter_chain():
    g = [vs_render.BlurGroup((10, 20, 100, 40), [(0.0, 1.5)])]
    f = vietsub_filter("9:16", "blur", g, "seg0001.ass")
    assert "[vblur]" in f and f.endswith("[vfit]ass=filename=seg0001.ass[v]")
    assert vietsub_filter("original", "blur", [], None).endswith("[v]")


def test_fit_transform():
    f = vs_render.fit_transform(1920, 1080, (1080, 1920), "crop")
    assert f.scale == pytest.approx(1920 / 1080) and f.oy == pytest.approx(0)
    f = vs_render.fit_transform(1920, 1080, (1080, 1920), "pad")
    assert f.scale == pytest.approx(1080 / 1920) and f.oy == pytest.approx((1920 - 607.5) / 2)
    f = vs_render.fit_transform(321, 181, None, "blur")
    assert (f.scale, f.out_w, f.out_h) == (1.0, 320, 180)


def test_build_ass_escapes_and_positions():
    fit = vs_render.fit_transform(640, 360, None, "blur")
    items = [(_ev(0, 1.5, vi="Dùng {\\b1} lệnh"), 0.0, 1.5), (_ev(2, 3, vi=""), 2.0, 3.0),
             (_ev(0, 30, static=True, vi="logo"), 0.0, 30.0)]
    ass = vs_render.build_ass(items, fit)
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogues) == 1  # bỏ câu chưa dịch và watermark
    assert dialogues[0].startswith("Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,{\\an5\\pos(300,315)}")
    assert dialogues[0].endswith("Dùng (/b1) lệnh")
    assert "PlayResX: 640" in ass and "PlayResY: 360" in ass


def test_build_srt():
    events = [_ev(0.5, 2.25, text="你好", vi="Xin chào"), _ev(3, 4, text="再见", vi=""),
              _ev(0, 60, text="台标", static=True)]
    assert vs_render.build_srt(events, "vi") == "1\n00:00:00,500 --> 00:00:02,250\nXin chào\n"
    assert vs_render.build_srt(events, "zh").count("-->") == 2


def test_write_text_atomic_srt_has_bom(tmp_path):
    p = tmp_path / "a.srt"
    vs_render.write_text_atomic(p, "1\nxin chào\n")
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list(tmp_path.glob("*.part"))


# ------------------------------------------------------------------ tham số / cài đặt


@pytest.fixture
def video_file(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    return p


def _params(path, **kw):
    return {"path": str(path), "segment_seconds": 60, "aspect": "9:16", "fit": "blur", "output_dir": "", **kw}


def test_validate_vietsub(video_file):
    validate(_params(video_file, segment_seconds=0, vietsub=True, vietsub_region="full"))
    for bad in ({"vietsub": "yes"}, {"vietsub": True, "vietsub_region": "top"}, {"segment_seconds": -1}):
        with pytest.raises(InvalidInput):
            validate(_params(video_file, **bad))


def test_plan_no_split():
    [s] = plan_segments(123.4, 0)
    assert (s.index, s.start, s.duration) == (1, 0, 123.4)


def test_folder_name_vietsub(tmp_path):
    assert folder_name(tmp_path / "a.mp4", "9:16", True) == "a - clip 9x16 vietsub"


def test_settings_defaults_and_validation():
    s = Settings()
    assert (s.clip_vietsub, s.clip_vietsub_region, s.translate_model) == (False, "bottom", "qwen2.5:7b")
    bad = Settings(clip_vietsub_region="top", ollama_url="  ", translate_model="").validated()
    assert (bad.clip_vietsub_region, bad.ollama_url, bad.translate_model) == (
        "bottom", "http://127.0.0.1:11434", "qwen2.5:7b")


# ------------------------------------------------------------------ chạy thật: OCR + FFmpeg

FONT = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
SUBS = [("今天天气很好", 0.5, 2.5), ("我们一起去公园吧", 3.0, 5.5)]


def _ocr_available() -> bool:
    try:
        vs_ocr.load_engine()
    except DependencyMissing:
        return False
    return True


needs_ocr = pytest.mark.skipif(not (FONT.is_file() and _ocr_available()),
                               reason="Cần RapidOCR (`uv sync --extra vietsub`) và font msyh.ttc")


def _make_subbed_video(tools, path: Path, seconds=6.0, size=(640, 360)) -> Path:
    """Video nền màu có phụ đề Trung (drawtext) ở 1/4 dưới, theo các mốc thời gian trong SUBS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    work = path.parent
    font = str(FONT).replace("\\", "/").replace(":", "\\:")
    draws = []
    for i, (text, s, e) in enumerate(SUBS):
        (work / f"sub{i}.txt").write_text(text, encoding="utf-8")
        draws.append(f"drawtext=fontfile='{font}':textfile=sub{i}.txt:fontsize=32:fontcolor=white:"
                     f"borderw=3:bordercolor=black:x=(w-tw)/2:y=h*0.78:enable='between(t,{s},{e})'")
    w, h = size
    cmd = [str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x4a6d8c:size={w}x{h}:rate=25:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
           "-vf", ",".join(draws), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-t", str(seconds), path.name]
    subprocess.run(cmd, check=True, capture_output=True, cwd=work, creationflags=CREATE_NO_WINDOW, timeout=120)
    return path


def _frame(tools, path: Path, t: float, w: int, h: int):
    import numpy as np

    out = subprocess.run([str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{t}", "-i", str(path),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                         check=True, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    return np.frombuffer(out.stdout, dtype=np.uint8).reshape(h, w, 3)


def _chinese_on_frame(engine, img) -> list[str]:
    h, w = img.shape[:2]
    geo = vs_ocr.SampleGeometry(w, h, 0, h, w, h)
    return [ln.text for ln in vs_ocr.FrameOCR(engine, geo)(img)]


@pytest.fixture
def services(data_dir):
    s = build_services(data_dir)
    yield s
    s.close()


@pytest.mark.ffmpeg
@needs_ocr
def test_scan_video_finds_timed_subtitles(ffmpeg_tools, tmp_path):
    src = _make_subbed_video(ffmpeg_tools, tmp_path / "scan.mp4")
    events = vs_ocr.scan_video(src, ffmpeg_tools, 640, 360, 6.0, "bottom")
    assert len(events) == 2, events
    for e, (text, s, t) in zip(events, SUBS):
        assert vs_ocr.similarity(e.text, text) >= 0.8, e.text
        assert abs(e.start - s) <= 0.5 and abs(e.end - t) <= 0.5
        assert e.box[1] > 360 * 0.7 and not e.static


@pytest.mark.ffmpeg
@needs_ocr
@pytest.mark.parametrize("aspect,size,folder", [("original", (640, 360), "goc"), ("9:16", (1080, 1920), "9x16")])
def test_vietsub_job_end_to_end(services, ffmpeg_tools, tmp_path, fake_ollama, aspect, size, folder):
    src = _make_subbed_video(ffmpeg_tools, tmp_path / UNICODE_DIR / "phim Trung.mp4")
    before = src.read_bytes()
    services.settings.ollama_url = fake_ollama
    job = services.jobs.submit(KIND, "test", _params(src, segment_seconds=0, aspect=aspect,
                                                     vietsub=True, vietsub_region="bottom"))
    assert services.jobs.wait_idle(300)
    job = services.job_repo.get(job.id)
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)

    r = job.result
    vs = r["vietsub"]
    assert (vs["lines"], vs["translated"], vs["failed"], vs["static"]) == (2, 2, [], 0)
    out_dir = Path(r["output_dir"])
    assert out_dir.name == f"phim Trung - clip {folder} vietsub"
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "phim Trung - vi.srt", "phim Trung - zh.srt", "phim Trung.mp4"]
    srt_vi = Path(vs["srt_vi"]).read_text(encoding="utf-8-sig")
    assert "Phụ đề Việt số 0" in srt_vi and "Phụ đề Việt số 1" in srt_vi and not has_cjk(srt_vi)
    assert has_cjk(Path(vs["srt_zh"]).read_text(encoding="utf-8-sig"))

    out = Path(r["files"][0]["path"])
    info = probe(out, ffmpeg_tools)
    assert (info.video[0].width, info.video[0].height) == size
    assert info.video[0].codec == "h264" and info.audio[0].codec == "aac"
    assert info.duration == pytest.approx(6.0, abs=0.5)

    engine = vs_ocr.load_engine()
    for _, s, t in SUBS:
        mid = (s + t) / 2
        assert _chinese_on_frame(engine, _frame(ffmpeg_tools, src, mid, 640, 360))  # nguồn có chữ Trung
        assert _chinese_on_frame(engine, _frame(ffmpeg_tools, out, mid, *size)) == []  # đầu ra đã che
    if aspect == "original":  # ngoài thời gian có chữ: không làm mờ — khung đầu ra gần như giống nguồn
        a = _frame(ffmpeg_tools, src, 2.75, 640, 360).astype(float)
        b = _frame(ffmpeg_tools, out, 2.75, 640, 360).astype(float)
        assert abs(a - b).mean() < 3
    assert src.read_bytes() == before  # file gốc không đổi
    assert len(_FakeOllama.chat_requests) == 1  # 2 câu trong 1 lô


@pytest.mark.ffmpeg
@needs_ocr
def test_vietsub_job_fails_fast_without_ollama(services, ffmpeg_tools, tmp_path):
    import socket

    src = _make_subbed_video(ffmpeg_tools, tmp_path / "v.mp4", seconds=2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        services.settings.ollama_url = f"http://127.0.0.1:{s.getsockname()[1]}"
    job = services.jobs.submit(KIND, "test", _params(src, vietsub=True))
    assert services.jobs.wait_idle(60)
    job = services.job_repo.get(job.id)
    assert job.status == JobStatus.FAILED and "Ollama" in job.error
    assert not (tmp_path / "v - clip 9x16 vietsub").exists()  # báo lỗi trước khi tạo gì


# ------------------------------------------------------------------ Ollama thật (tùy chọn)


@pytest.mark.ollama
@pytest.mark.skipif(os.environ.get("PAS_TEST_OLLAMA") != "1",
                    reason="Đặt PAS_TEST_OLLAMA=1 để thử với Ollama + qwen2.5:7b thật (~30 s trên CPU)")
def test_real_ollama_translation():
    client = OllamaClient()
    client.ensure_ready()
    tr = translate_lines(client, {0: "今天天气很好", 1: "我们一起去公园吧"})
    assert set(tr.vi) | set(tr.failed) == {0, 1}
    assert tr.vi and all(not has_cjk(v) for v in tr.vi.values())
