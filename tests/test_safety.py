"""Kiểm duyệt: hậu xử lý NudeNet, khớp tiền xử lý với thư viện gốc, CLIP, lọc từ, mặt nạ, báo cáo, tải mô hình
(máy chủ HTTP cục bộ) và chạy thật cả tác vụ trên video tổng hợp với bộ nhận diện giả theo thời gian."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from conftest import UNICODE_DIR
from pas.core.errors import DependencyMissing, ExternalToolError, InvalidInput, JobCancelled
from pas.jobs.models import JobStatus
from pas.media.ffmpeg import CREATE_NO_WINDOW
from pas.modules.clips import tasks as clip_tasks
from pas.modules.clips.tasks import KIND, Segment, encode_args, validate, vietsub_filter
from pas.modules.safety import detect, mask, models, report, scan
from pas.modules.safety.scan import Detection
from pas.modules.safety.words import DEFAULT_FILE, MASK, WordFilter, ensure_user_list, parse_words, word_list_path
from pas.services import build_services

HAS_ORT = importlib.util.find_spec("onnxruntime") is not None
HAS_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("nudenet") is not None

# ------------------------------------------------------------------ NudeNet


def _raw(rows):
    """rows: (cx, cy, w, h, lớp, điểm) theo tọa độ 320 → đầu ra thô (1, 22, N) như 320n.onnx."""
    out = np.zeros((1, 4 + len(detect.NUDENET_LABELS), len(rows)), np.float32)
    for i, (cx, cy, w, h, cls, score) in enumerate(rows):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + detect.NUDENET_LABELS.index(cls), i] = score
    return out


def test_nms_keeps_best_of_overlapping():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    assert sorted(detect.nms(boxes, np.array([0.5, 0.9, 0.3]))) == [1, 2]


def test_postprocess_scales_clips_and_thresholds():
    out = _raw([(160, 160, 64, 64, "FEMALE_BREAST_EXPOSED", 0.8),
                (162, 161, 64, 64, "FEMALE_BREAST_EXPOSED", 0.6),  # trùng → NMS bỏ
                (310, 20, 40, 40, "FACE_FEMALE", 0.5),  # tràn mép phải → cắt
                (100, 100, 20, 20, "BUTTOCKS_EXPOSED", 0.2)])  # dưới ngưỡng
    # ảnh gốc 640×360 → đệm thành 640×640, hệ số 2
    got = detect.nudenet_postprocess(out, 640, 0.25, 640, 360)
    assert [(c, s) for c, s, _ in got] == [("FEMALE_BREAST_EXPOSED", 0.8), ("FACE_FEMALE", 0.5)]
    assert got[0][2] == pytest.approx((256, 256, 384, 360))  # cắt theo chiều cao khung
    assert got[1][2] == pytest.approx((580, 0, 640, 80))
    # hộp nằm hẳn trong phần đệm (dưới y=360) bị bỏ
    assert detect.nudenet_postprocess(_raw([(160, 300, 20, 20, "FACE_MALE", 0.9)]), 640, 0.25, 640, 360) == []


@pytest.mark.skipif(not HAS_CV2, reason="Cần cv2 + nudenet (cài kèm `uv sync --extra safety`) để so với thư viện gốc")
@pytest.mark.parametrize("shape", [(360, 640), (1280, 720), (333, 517), (100, 100), (2000, 300)])
def test_nudenet_input_matches_reference_library(shape):
    from nudenet import nudenet as ref

    rng = np.random.default_rng(sum(shape))
    rgb = rng.integers(0, 256, (*shape, 3), dtype=np.uint8)
    rgb[: shape[0] // 2] = rgb[: shape[0] // 2, :1]  # vừa nhiễu vừa vùng trơn
    expected = ref._read_image(np.ascontiguousarray(rgb[:, :, ::-1]))[0]  # thư viện nhận BGR (cv2.imread)
    got = detect.nudenet_input(rgb)
    assert got.shape == expected.shape == (1, 3, 320, 320)
    assert np.abs(got - expected).max() <= 1.01 / 255  # cv2 dùng số nguyên cố định: lệch tối đa 1 mức xám


@pytest.mark.skipif(not (HAS_ORT and importlib.util.find_spec("nudenet")), reason="Cần `uv sync --extra safety`")
def test_nudity_detector_runs_on_plain_frame():
    d = detect.NudityDetector(models.nudenet_model(), scan.NUDITY_THRESHOLD)
    frame = np.full((360, 640, 3), 128, np.uint8)
    assert d(frame) == []


# ------------------------------------------------------------------ CLIP


def test_violence_score_sums_unsafe_probabilities():
    n_u, n_s = len(detect.UNSAFE_PROMPTS), len(detect.SAFE_PROMPTS)
    text = np.eye(n_u + n_s, 8 + n_u + n_s, dtype=np.float32)
    score, top = detect.violence_score(text[2] * 3.0, text)  # ảnh trùng câu UNSAFE thứ 3
    assert score > 0.99 and top == 2
    score, _ = detect.violence_score(text[n_u + 1], text)  # trùng một câu SAFE
    assert score < 0.01


def test_prompt_embeddings_match_prompt_lists():
    text = np.load(detect.PROMPTS_FILE)
    assert text.shape[0] == len(detect.UNSAFE_PROMPTS) + len(detect.SAFE_PROMPTS)
    assert len(detect.UNSAFE_LABELS) == len(detect.UNSAFE_PROMPTS)


def test_violence_runs_need_consecutive_samples():
    samples = [(0, 0.9, "a"), (1, 0.1, "b"), (2, 0.7, "c"), (3, 0.8, "d"), (4, 0.65, "e"), (5, 0.2, "f"),
               (6, 0.95, "g")]
    got = scan.violence_runs(samples, threshold=0.6, min_run=2)
    assert [d.t for d in got] == [2, 3, 4]  # mẫu lẻ loi ở 0 và 6 bị bỏ
    assert all(d.kind == "violence" and d.box is None for d in got)


# ------------------------------------------------------------------ Từ cấm


def test_word_filter_cjk_substring_and_latin_whole_word():
    f = WordFilter(parse_words("傻逼\nlồn\nfuck # tiếng Anh\nđịt mẹ\nFUCK\n"))
    assert f.words == ["傻逼", "lồn", "fuck", "địt mẹ"]  # bỏ chú thích, chữ thường, bỏ trùng
    assert f.find("你这个傻逼啊") == ["傻逼"]
    assert f.find("mở lon nước") == [] and f.find("Lồn") == ["Lồn"]
    assert f.find("Fucking hell") == []  # không phải nguyên từ
    assert f.find("what the FUCK!") == ["FUCK"]
    assert f.mask("ĐỊT MẸ mày") == f"{MASK} mày"
    assert WordFilter([]).find("bất kỳ") == []


def test_word_filter_normalizes_decomposed_vietnamese():
    import unicodedata

    f = WordFilter(["lồn"])
    assert f.find(unicodedata.normalize("NFD", "đồ lồn")) == ["lồn"]


def test_default_word_list_loads_and_user_copy_is_kept(tmp_path):
    f = WordFilter.load(DEFAULT_FILE)
    assert len(f.words) > 50 and f.find("卧槽")
    assert word_list_path(tmp_path) == DEFAULT_FILE
    user = ensure_user_list(tmp_path)
    assert word_list_path(tmp_path) == user
    user.write_text("chỉ một từ\n", encoding="utf-8")
    ensure_user_list(tmp_path)  # không ghi đè bản người dùng đã sửa
    assert WordFilter.load(user).words == ["chỉ một từ"]


# ------------------------------------------------------------------ Mặt nạ, bộ lọc, tham số


def test_detection_round_trip():
    for d in (Detection(1.25, "nudity", "x", 0.5, (1.0, 2.0, 3.0, 4.0), 0.5),
              Detection(3.0, "violence", "y", 0.7, None, 1.0)):
        assert Detection.from_dict(d.to_dict()) == d


def test_frame_rects_timing_and_scaling():
    dets = [Detection(2.0, "nudity", "x", 0.9, (100, 100, 300, 200), 0.5),
            Detection(5.0, "violence", "y", 0.8, None, 1.0)]
    w, h, n, rects = mask.frame_rects(dets, 8.0, 1280, 720)
    assert (w, h, n) == (640, 360, 33)
    assert sorted(k for k, r in rects.items() if (50, 50, 150, 100) in r) == list(range(6, 11))  # [1.5, 2.75)
    assert sorted(k for k, r in rects.items() if (0, 0, 640, 360) in r) == list(range(16, 25))  # [4.0, 6.25)
    assert 5 not in rects and 11 not in rects


def test_safety_graph_and_vietsub_filter_chain():
    g = mask.safety_graph(640, 360, "[0:v:0]", "[1:v]", "[vsafe]")
    assert g.startswith("[0:v:0]scale=640:360") and "alphamerge" in g and g.endswith("[vsafe]")
    chain = vietsub_filter("9:16", "blur", [], None, (640, 360))
    assert chain.startswith("[0:v:0]") and "[1:v]" in chain and "[vsafe]" in chain and chain.endswith("[v]")
    assert "[1:v]" not in vietsub_filter("9:16", "blur", [], None)


def test_encode_args_mask_is_input_cut_at_same_point():
    args = encode_args(Path("src.mp4"), Segment(2, 60.0, 60.0), "9:16", "blur", Path("o.part"), "s.filter",
                       mask.MASK_FILE)
    i = args.index(mask.MASK_FILE)
    assert args[i - 3:i] == ["-ss", "60.000", "-i"]
    assert args.index("-t") > i  # -t sau mọi đầu vào → là tùy chọn đầu ra
    assert mask.MASK_FILE not in encode_args(Path("src.mp4"), Segment(1, 0, 5), "9:16", "blur", Path("o"))


def test_validate_safety(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    base = {"path": str(src), "segment_seconds": 60, "aspect": "9:16", "fit": "blur", "output_dir": ""}
    validate({**base, "safety": {"nudity": True, "violence": False, "text": False}})
    for bad in ({"nudity": "yes"}, {"weapons": True}, {"nudity": False, "text": False}, ["nudity"]):
        with pytest.raises(InvalidInput):
            validate({**base, "safety": bad})


# ------------------------------------------------------------------ Khoảng thời gian + báo cáo


def test_intervals_merge_and_report():
    dets = [Detection(1.0, "nudity", "ngực nữ lộ", 0.6, (0, 0, 1, 1), 0.5),
            Detection(1.5, "nudity", "mông lộ", 0.8, (0, 0, 1, 1), 0.5),
            Detection(70.0, "text", "đm", 0.9, (0, 0, 1, 1), 1.0)]
    ivs = scan.intervals(dets, 100.0)
    assert [(i.kind, i.start, i.end) for i in ivs] == [("nudity", 0.5, 2.0), ("text", 69.0, 71.0)]
    assert ivs[0].labels == ["ngực nữ lộ", "mông lộ"] and ivs[0].score == 0.8
    s = report.summarize(dets, 100.0)
    assert s["counts"] == {"nudity": 1, "violence": 0, "text": 1} and s["seconds"]["text"] == 2.0

    segs = [Segment(1, 0, 60), Segment(2, 60, 40)]
    text = report.build_report("a.mp4", {"nudity": True, "text": True}, dets, 100.0, segs, masked_lines=2)
    assert report.DISCLAIMER in text and "KHÔNG bảo đảm" in text
    assert "0:00.5–0:02.0 [phần 1]  Khỏa thân: ngực nữ lộ, mông lộ" in text
    assert "1:09.0–1:11.0 [phần 2]  Chữ thô tục: đm" in text
    assert "2 câu có từ thô tục" in text and "Bạo lực" not in text.split("\n")[4]
    assert "Không phát hiện" in report.build_report("a.mp4", {"nudity": True}, [], 10.0, segs[:1])


# ------------------------------------------------------------------ Tải mô hình


class _Ctx:
    def __init__(self, cancel_after: int | None = None):
        self.calls, self.cancel_after, self.logs = 0, cancel_after, []

    def checkpoint(self):
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            raise JobCancelled()

    def report(self, *_):
        pass

    def log(self, msg):
        self.logs.append(msg)


@pytest.fixture
def model_server():
    payload = bytes(range(256)) * 9000  # ~2.3 MB, nhiều khúc 1 MB

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/m.onnx", payload
    srv.shutdown()


def _model(url, payload, **kw):
    return models.ModelFile(**{"key": "t", "title": "Thử", "filename": "t.onnx", "url": url,
                               "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
                               "license": "MIT", "source": "local", **kw})


def test_download_verifies_and_installs(model_server, tmp_path):
    url, payload = model_server
    m = _model(url, payload)
    with pytest.raises(DependencyMissing):
        models.require_model(tmp_path, m)
    path = models.download(m, tmp_path, _Ctx())
    assert path.read_bytes() == payload and models.require_model(tmp_path, m) == path
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.onnx"]
    ctx = _Ctx()
    models.download(m, tmp_path, ctx)  # đã có → không tải lại
    assert ctx.calls == 0


@pytest.mark.parametrize("bad, msg", [({"sha256": "0" * 64}, "SHA-256"), ({"size": 1000}, "lớn hơn dự kiến"),
                                      ({"size": 10 ** 8}, "chưa đủ")])
def test_download_rejects_bad_file(model_server, tmp_path, bad, msg):
    url, payload = model_server
    with pytest.raises(ExternalToolError, match=msg):
        models.download(_model(url, payload, **bad), tmp_path, _Ctx())
    assert list(tmp_path.iterdir()) == []  # không để lại file .part hay file hỏng


def test_download_cancel_and_unreachable(model_server, tmp_path):
    url, payload = model_server
    with pytest.raises(JobCancelled):
        models.download(_model(url, payload), tmp_path, _Ctx(cancel_after=1))
    assert list(tmp_path.iterdir()) == []
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}/m.onnx"
    with pytest.raises(ExternalToolError, match="Không tải được"):
        models.download(_model(dead, payload), tmp_path, _Ctx())


def test_validate_download():
    models.validate_download({"model": models.CLIP_VISION.key})
    with pytest.raises(InvalidInput):
        models.validate_download({"model": "../evil"})


# ------------------------------------------------------------------ Cả tác vụ (FFmpeg thật, bộ nhận diện giả)


class _ByTime:
    """Bộ nhận diện giả trả kết quả theo thời điểm của khung mẫu (khung thứ i ↔ i / SAMPLE_FPS giây)."""

    def __init__(self, every: int, fn):
        self.i, self.every, self.fn = 0, every, fn

    def __call__(self, rgb):
        t = self.i * self.every / scan.SAMPLE_FPS
        self.i += 1
        assert rgb.dtype == np.uint8 and rgb.shape == (360, 640, 3)
        return self.fn(t)


def _fake_detectors(opts, models_dir, app_root, ctx):
    return {
        "nudity": _ByTime(1, lambda t: [("FEMALE_BREAST_EXPOSED", 0.9, (200.0, 100.0, 360.0, 220.0))]
                          if 2.0 <= t < 3.0 else []),
        "violence": _ByTime(scan.CLIP_EVERY, lambda t: (0.9, "máu me") if t in (6.0, 7.0) else (0.1, "máu me")),
    }


def _frame(tools, path: Path, t: float, w=640, h=360):
    out = subprocess.run([str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{t}", "-i", str(path),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         check=True, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    return np.frombuffer(out.stdout, np.uint8).reshape(h, w, 3).astype(float)


@pytest.fixture
def services(data_dir):
    s = build_services(data_dir)
    yield s
    s.close()


@pytest.mark.ffmpeg
def test_safety_job_blurs_only_detected_regions_and_times(services, ffmpeg_tools, tmp_path, monkeypatch):
    src = tmp_path / UNICODE_DIR / "video gốc.mp4"
    src.parent.mkdir(parents=True)
    subprocess.run([str(ffmpeg_tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=25:duration=10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "12", "-pix_fmt", "yuv420p", str(src)],
                   check=True, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
    before = src.read_bytes()
    monkeypatch.setattr(clip_tasks, "_load_detectors", _fake_detectors)
    job = services.jobs.submit(KIND, "test", {
        "path": str(src), "segment_seconds": 5, "aspect": "original", "fit": "blur", "output_dir": "",
        "safety": {"nudity": True, "violence": True, "text": False}})
    assert services.jobs.wait_idle(300)
    job = services.job_repo.get(job.id)
    assert job.status == JobStatus.COMPLETED, (job.error, job.error_hint)

    sf = job.result["safety"]
    assert sf["counts"] == {"nudity": 1, "violence": 1, "text": 0}
    assert [(i["kind"], i["start"], i["end"]) for i in sf["intervals"]] == [("nudity", 1.5, 3.25),
                                                                          ("violence", 5.0, 8.0)]
    rep = Path(sf["report"])
    assert rep.name == "video gốc - kiểm duyệt.txt"
    text = rep.read_text(encoding="utf-8-sig")
    assert "[phần 1]  Khỏa thân: ngực nữ lộ" in text and "[phần 2]  Bạo lực / máu me: máu me" in text
    part1, part2 = (Path(f["path"]) for f in job.result["files"])

    def diff(out, t_out, t_src, region=(slice(None), slice(None))):
        return np.abs(_frame(ffmpeg_tools, out, t_out)[region] - _frame(ffmpeg_tools, src, t_src)[region]).mean()

    box = (slice(110, 210), slice(210, 350))  # bên trong hộp khỏa thân (đã nới 15%)
    outside = (slice(250, 360), slice(0, 640))
    assert diff(part1, 1.0, 1.0) < 3  # trước khi phát hiện: giữ nguyên
    assert diff(part1, 2.5, 2.5, box) > 15  # trong hộp: mờ
    assert diff(part1, 2.5, 2.5, outside) < 3  # ngoài hộp: giữ nguyên
    assert diff(part2, 1.5, 6.5, outside) > 15  # bạo lực: cả khung mờ (mặt nạ cắt đúng chỗ với -ss 5)
    assert diff(part2, 4.6, 9.6) < 3  # sau đoạn bạo lực: giữ nguyên
    assert src.read_bytes() == before  # file gốc không đổi
    assert not (services.paths.temp_dir / f"job-{job.id}").exists()  # thư mục tạm (cả mặt nạ) đã dọn


@pytest.mark.ffmpeg
def test_safety_job_fails_fast_when_violence_model_missing(services, ffmpeg_tools, tmp_path):
    if not (HAS_ORT and importlib.util.find_spec("PIL")):
        pytest.skip("Cần `uv sync --extra safety`")
    from conftest import make_media

    src = make_media(ffmpeg_tools, tmp_path / "v.mp4", seconds=1)
    job = services.jobs.submit(KIND, "test", {
        "path": str(src), "segment_seconds": 0, "aspect": "9:16", "fit": "blur", "output_dir": "",
        "safety": {"nudity": False, "violence": True, "text": False}})
    assert services.jobs.wait_idle(60)
    job = services.job_repo.get(job.id)
    assert job.status == JobStatus.FAILED and "CLIP" in job.error and "Tải mô hình" in job.error_hint
    assert not (tmp_path / "v - clip 9x16").exists()


def test_ocr_engine_no_spinning():
    """RapidOCR không có tùy chọn tắt spin: load_engine tạm thay hàm tạo SessionOptions rồi trả lại như cũ."""
    pytest.importorskip("rapidocr_onnxruntime")
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession

    from pas.modules.vietsub.ocr import load_engine

    before = OrtInferSession.__dict__["_init_sess_opts"]
    engine = load_engine()
    for part in (engine.text_det, engine.text_cls, engine.text_rec):
        ort_session = (part.infer if hasattr(part, "infer") else part.session).session
        opts = ort_session.get_session_options()
        assert opts.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert OrtInferSession.__dict__["_init_sess_opts"].__func__ is before.__func__
