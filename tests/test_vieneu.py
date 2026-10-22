"""Giọng đọc VieNeu (bước 2 "Làm lại video"): danh sách giọng có sẵn, kiểm tra tham số, chuẩn bị file mẫu bằng FFmpeg,
đọc kịch bản với engine giả lập, chạy cả tác vụ; và một test chạy mô hình thật (bỏ qua nếu chưa tải mô hình)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from pas.core.errors import ExternalToolError, InvalidInput
from pas.core.paths import AppPaths, default_data_dir
from pas.jobs.context import JobContext, JobControl
from pas.media.ffmpeg import probe
from pas.modules.remake import models, project, tasks, tts, vieneu_tts
from pas.modules.safety.models import is_installed
from pas.services import build_services
from conftest import make_media

pytestmark = pytest.mark.skipif(not vieneu_tts.available(), reason="Chưa cài vieneu (uv sync --extra tts)")


class FakeEngine:
    """Mỗi câu trả 0,5 s tín hiệu (có biên độ vượt ±1 để kiểm tra cắt ngưỡng); ghi lại các lượt gọi."""

    def __init__(self):
        self.calls = []
        self.refs = []

    def encode_reference(self, path, denoise=True):
        self.refs.append(path)
        return np.ones(4, dtype=np.float32), np.zeros((1, 8), dtype=np.int64)

    def infer(self, text, voice=None):
        self.calls.append((text, voice))
        n = vieneu_tts.SAMPLE_RATE // 2
        return np.linspace(-1.5, 1.5, n, dtype=np.float32)


def _ctx(params, tmp):
    logs = []
    tmp.mkdir(parents=True, exist_ok=True)
    return JobContext(1, params, tmp, JobControl(), lambda p, m: None, lambda lv, m: logs.append((lv, m))), logs


def _project(tmp_path) -> Path:
    proj = tmp_path / "p - làm lại"
    proj.mkdir()
    project.save(proj, {"version": 1, "source": str(tmp_path / "v.mp4"),
                        "script": [{"id": 0, "role": "hook", "text": "Xin chào các bạn.", "src": [0, 0]}]})
    return proj


def test_presets_featured_first():
    names = [p.name for p in vieneu_tts.presets()]
    assert len(names) > 10 and "Adam bựa" in names
    assert names[0] == "Adam bựa"  # giọng nổi bật số 1
    assert all(p.description for p in vieneu_tts.presets()[:5])


def test_validate_vieneu_params(tmp_path):
    proj = _project(tmp_path)
    base = {"project_dir": str(proj), "text": "Xin chào.", "voice": vieneu_tts.KEY}
    tasks.validate_voice({**base, "preset": "Adam bựa"})
    with pytest.raises(InvalidInput, match="Giọng VieNeu không hợp lệ"):
        tasks.validate_voice({**base, "preset": "không có"})
    with pytest.raises(InvalidInput, match="Không tìm thấy file giọng mẫu"):
        tasks.validate_voice({**base, "sample": str(tmp_path / "mất.wav")})
    bad = tmp_path / "mẫu.txt"
    bad.write_text("x")
    with pytest.raises(InvalidInput, match="không phải âm thanh"):
        tasks.validate_voice({**base, "sample": str(bad)})
    ok = tmp_path / "mẫu.mp3"
    ok.write_bytes(b"x")
    tasks.validate_voice({**base, "sample": str(ok)})  # nội dung kiểm tra lúc chạy (ffprobe)


def test_prepare_sample_converts_and_rejects_short(tmp_path, ffmpeg_tools):
    short = make_media(ffmpeg_tools, tmp_path / "ngắn.wav", seconds=2.0, video=False)
    with pytest.raises(InvalidInput, match="quá ngắn"):
        vieneu_tts.prepare_sample(short, ffmpeg_tools, tmp_path / "out1.wav")
    silent_video = make_media(ffmpeg_tools, tmp_path / "không tiếng.mp4", seconds=4.0, audio=False)
    with pytest.raises(InvalidInput, match="không có âm thanh"):
        vieneu_tts.prepare_sample(silent_video, ffmpeg_tools, tmp_path / "out2.wav")

    src = make_media(ffmpeg_tools, tmp_path / "mẫu dài.mp4", seconds=10.0)
    before = src.read_bytes()
    out = tmp_path / "job" / "mẫu.wav"
    out.parent.mkdir()
    used = vieneu_tts.prepare_sample(src, ffmpeg_tools, out)
    assert used == vieneu_tts.MAX_SAMPLE
    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
        assert abs(w.getnframes() / 48000 - vieneu_tts.MAX_SAMPLE) < 0.05
    assert src.read_bytes() == before  # file mẫu gốc không đổi
    assert sorted(p.name for p in out.parent.iterdir()) == ["mẫu.wav"]  # không còn file .part


def test_synthesize_preset_and_clone(tmp_path):
    text = "Câu một. Câu hai!\n\nĐoạn hai."
    eng = FakeEngine()
    res = vieneu_tts.synthesize(eng, text, tts.TONES["natural"], tmp_path / "a.wav", preset="Adam bựa")
    assert [c for c in eng.calls] == [("Câu một.", "Adam bựa"), ("Câu hai!", "Adam bựa"), ("Đoạn hai.", "Adam bựa")]
    assert res["sample_rate"] == 48000 and len(res["segments"]) == 2
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert w.getframerate() == 48000
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert pcm.max() == 32767 and pcm.min() == -32767  # biên độ vượt ±1 bị cắt, không tràn số

    eng = FakeEngine()
    sample = tmp_path / "mẫu.wav"
    vieneu_tts.synthesize(eng, text, tts.TONES["fast"], tmp_path / "b.wav", sample=sample)
    assert eng.refs == [str(sample)]  # lấy đặc trưng giọng một lần cho cả kịch bản
    assert all(isinstance(v, dict) and set(v) == {"speaker_emb", "codes"} for _, v in eng.calls)


def test_synthesize_engine_error_leaves_no_file(tmp_path):
    class Broken(FakeEngine):
        def infer(self, text, voice=None):
            raise RuntimeError("onnx hỏng")

    out = tmp_path / "x.wav"
    with pytest.raises(ExternalToolError, match="VieNeu lỗi khi đọc câu"):
        vieneu_tts.synthesize(Broken(), "Xin chào.", tts.TONES["natural"], out, preset="Adam bựa")
    assert not list(tmp_path.iterdir())


def test_load_requires_model(tmp_path):
    from pas.core.errors import DependencyMissing

    with pytest.raises(DependencyMissing):
        models.require_vieneu(tmp_path)


def test_voice_handler_clone_with_fake_engine(tmp_path, ffmpeg_tools, monkeypatch):
    proj = _project(tmp_path)
    sample = make_media(ffmpeg_tools, tmp_path / "aivoices.m4a", seconds=5.0, video=False)
    eng = FakeEngine()
    monkeypatch.setattr(models, "require_vieneu", lambda d: Path(d) / models.VIENEU_DIR)
    monkeypatch.setattr(vieneu_tts, "load", lambda root: eng)
    sv = build_services(tmp_path / "data", start_jobs=False)
    try:
        params = {"project_dir": str(proj), "text": "Xin chào các bạn.\n\nHôm nay nói về đá quý.",
                  "voice": vieneu_tts.KEY, "tone": "natural", "sample": str(sample)}
        spec = sv.registry.get(tasks.KIND_VOICE)
        spec.validate(params)
        ctx, logs = _ctx(params, tmp_path / "job")
        res = spec.handler(ctx)
    finally:
        sv.close()
    assert res["voice"] == "vieneu" and res["sample"] == "aivoices.m4a" and res["preset"] is None
    assert res["segments"] == 2 and res["matched"] == 1 and res["license"].startswith("Apache-2.0")
    meta = json.loads((proj / project.VOICE_JSON).read_text(encoding="utf-8"))
    assert "nhân bản" in meta["credit"] and meta["sample_rate"] == 48000
    assert project.load(proj)["voice"]["credit"] == meta["credit"]
    assert any("phải có quyền" in m for _, m in logs)
    assert len(eng.refs) == 1 and Path(eng.refs[0]).parent == tmp_path / "job"  # mẫu đã chuyển nằm trong thư mục tác vụ


def test_model_download_registered(tmp_path):
    sv = build_services(tmp_path / "data", start_jobs=False)
    try:
        sv.registry.get("model.download").validate({"model": models.VIENEU.key})
    finally:
        sv.close()


REAL_MODELS = AppPaths(default_data_dir()).models_dir


@pytest.mark.skipif(not is_installed(REAL_MODELS, models.VIENEU), reason="Chưa tải mô hình VieNeu")
def test_real_model_preset(tmp_path):
    engine = vieneu_tts.load(REAL_MODELS / models.VIENEU_DIR)
    out = tmp_path / "adam.wav"
    res = vieneu_tts.synthesize(engine, "Xin chào các bạn.", tts.TONES["natural"], out, preset="Adam bựa")
    assert 0.8 < res["duration"] < 6
    with wave.open(str(out)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    assert np.sqrt(np.mean(pcm ** 2)) > 300  # có tiếng, không phải im lặng
