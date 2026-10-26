"""Phim ngắn AI: kịch bản (phân tích/kiểm tra/Ollama giả lập), gán giọng, mô tả ảnh, dòng thời gian, âm thanh;
và chạy thật cả 3 bước (viết → vẽ → dựng) với LLM giả, máy vẽ ảnh giả, giọng giả (WAV tự sinh) + FFmpeg thật."""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from pas.core.config import Settings
from pas.core.errors import ExternalToolError, InvalidInput
from pas.jobs.context import JobContext, JobControl
from pas.media.ffmpeg import probe
from pas.modules.aifilm import models, project, render, story, tasks


class FakeLLM:
    model = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def chat_json(self, user, max_tokens, temperature, ctx=None, system="", schema=None, num_ctx=None):
        self.calls.append(user)
        return self.answers.pop(0) if self.answers else {}


class FakeGenerator:
    """Ảnh màu đơn sắc theo seed — đủ để kiểm tra luồng dữ liệu và FFmpeg."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, seed, steps, cancelled):
        from PIL import Image

        self.prompts.append((prompt, seed, steps))
        return Image.new("RGB", (512, 912), (seed % 256, (seed >> 8) % 256, (seed >> 16) % 256))


def _ctx(params, tmp):
    logs = []
    tmp.mkdir(parents=True, exist_ok=True)
    return JobContext(1, params, tmp, JobControl(), lambda p, m: None, lambda lv, m: logs.append((lv, m))), logs


def _answer(shots=6):
    return {
        "title": "The Silver Secret",
        "style": "cinematic film still, fantasy academy",
        "characters": [{"name": "Lyra", "gender": "female", "look": "young woman, silver hair"},
                       {"name": "Kael", "gender": "male", "look": "tall man in black armor"}],
        "shots": [
            {"speaker": "Lyra" if i % 3 == 0 else ("Kael" if i % 3 == 1 else ""),
             "image": f"close-up of Lyra and Kael, scene {i}", "motion": "shake" if i == 1 else "bogus",
             "en": f"Line number {i}." if i % 3 != 2 else "", "vi": f"Câu số {i}." if i % 3 != 2 else ""}
            for i in range(shots)],
    }


SCRIPT = """TIÊU ĐỀ: Test
PHONG CÁCH: cinematic
# ghi chú bị bỏ qua
NHÂN VẬT: An | nữ | kristin | young woman, red scarf
NHÂN VẬT: Bao | nam | libritts_r#36 | old man

CẢNH 1 | zoom_in
NGƯỜI NÓI: An
ẢNH: An looks at Bao, An smiles
EN: Hello.
VI: Xin chào.

CẢNH 2 | flash
NGƯỜI NÓI:
ẢNH: wide shot of a castle
EN:
VI:
"""


# ------------------------------------------------------------------ kịch bản


def test_parse_format_round_trip_and_prompt():
    st = story.parse_story(SCRIPT)
    assert [c.voice for c in st.characters] == ["kristin", "libritts_r#36"]
    assert story.voice_parts("libritts_r#36") == ("libritts_r", 36) and story.voice_parts("john") == ("john", None)
    assert story.parse_story(story.format_story(st)) == st
    # tên → ngoại hình lần đầu, "the man/the woman" các lần sau; thêm phong cách chung
    assert story.image_prompt(st, st.shots[0]) == "young woman, red scarf looks at old man, the woman smiles, cinematic"
    assert story.shot_seed(st, st.shots[0]) == story.shot_seed(st, st.shots[0], 0) != story.shot_seed(st, st.shots[0], 1)


@pytest.mark.parametrize("bad, msg", [
    (SCRIPT.replace("NGƯỜI NÓI: An", "NGƯỜI NÓI: Ghost"), "không có trong danh sách"),
    (SCRIPT.replace("VI: Xin chào.", "VI:"), "thiếu phụ đề"),
    (SCRIPT.replace("ẢNH: wide shot of a castle", "ẢNH:"), "thiếu dòng 'ẢNH:'"),
    (SCRIPT.replace("TIÊU ĐỀ: Test", ""), "TIÊU ĐỀ"),
    (SCRIPT.replace("| kristin |", "| alan |"), ""),
])
def test_parse_rejects_bad_scripts(bad, msg):
    with pytest.raises(InvalidInput) as exc:
        story.parse_story(bad)
    assert msg in exc.value.message


def test_write_story_retries_then_assigns_distinct_voices():
    llm = FakeLLM({"title": "x", "characters": [], "shots": []}, _answer())
    st = story.write_story(llm, "a girl with a secret", 6)
    assert len(llm.calls) == 2 and "previous answer had problems" in llm.calls[1]
    assert [c.voice for c in st.characters] == ["kristin", "john"]
    assert st.shots[1].motion == "shake" and st.shots[0].motion == story.DEFAULT_MOTION  # chuyển động lạ → mặc định
    assert st.shots[2].speaker == "" and st.shots[2].en == ""
    many = [story.Character(f"M{i}", "nam", "", "") for i in range(4)] + [story.Character("F", "nữ", "", ""),
                                                                           story.Character("G", "nữ", "", "")]
    story.assign_voices(many)
    assert len({c.voice for c in many}) == 6  # không hai nhân vật nào cùng giọng


def test_problems_catch_stage_directions_and_untranslated_subtitles():
    # các câu lỗi thật qwen2.5:7b đã viết ở lần chạy thật
    names = {"silver", "prince"}
    assert story._is_stage_direction("Prince smirking, tugging Silver's robe", names)
    assert story._is_stage_direction("Prince's eyes widen in horror", names)
    assert story._is_stage_direction("Silver and Prince walk away from the school", names)
    assert not story._is_stage_direction("Prince, you will regret this!", names)
    assert not story._is_stage_direction("Silver is mine, do you hear me?", names)
    assert not story._is_stage_direction("They never see the real me.", names)
    assert story._untranslated("Prince smirking, tugging Silver's robe", "Hoàng tử nhếch môi, tuggingSilver's áo",
                               names) == "tugging"
    assert story._untranslated("They never see the real me.", "Họ không bao giờ thấy con người thật của tôi.",
                               names) == ""
    data = _answer()
    data["shots"][0]["en"], data["shots"][0]["speaker"] = "Lyra smiles at Kael", "Lyra"
    assert any("describes an action" in p for p in story._problems(data, 6))
    data = _answer()  # tên tiếng Việt → giọng tiếng Anh đọc sai
    data["characters"][0]["name"] = "Bạch Hạc"
    data["shots"][0]["speaker"] = data["shots"][3]["speaker"] = "Bạch Hạc"
    assert any("not English" in p for p in story._problems(data, 6))
    assert not story._problems(_answer(), 6)


def test_write_story_gives_up_with_real_error():
    with pytest.raises(ExternalToolError):
        story.write_story(FakeLLM({}, {}, {}), "idea idea idea", 6)


# ------------------------------------------------------------------ dòng thời gian / âm thanh


def _tone(path, seconds, rate=22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(seconds * rate))))


def test_timeline_and_voice_track(tmp_path):
    a = tmp_path / "a.wav"
    _tone(a, 3.0)
    items = render.timeline([(1, a, 3.0, "vi", "en"), (2, None, 0.0, "", ""), (3, a, 0.5, "x", "y")])
    assert [it.duration for it in items] == [pytest.approx(3.55, abs=0.04), render.MIN_SHOT, render.MIN_SHOT]
    assert items[1].start == pytest.approx(items[0].duration, abs=1e-3)
    rate = render.build_voice_track(items, tmp_path / "v.wav")
    with wave.open(str(tmp_path / "v.wav"), "rb") as w:
        assert rate == 22050 and w.getnframes() == round(render.total_duration(items) * rate)
    other = tmp_path / "b.wav"
    _tone(other, 1.0, rate=16000)
    with pytest.raises(ExternalToolError):
        render.build_voice_track(render.timeline([(1, a, 3.0, "", ""), (2, other, 1.0, "", "")]), tmp_path / "x.wav")
    ass = render.build_ass(items, (720, 1280), "Tiêu {đề}", True, True)
    assert render.AI_LABEL in ass and "Tiêu (đề)" in ass and r"\N{\rEn}en" in ass


def test_validators():
    with pytest.raises(InvalidInput):
        tasks.validate_story({"idea": "ngắn"})
    with pytest.raises(InvalidInput):
        tasks.validate_story({"idea": "một ý tưởng đủ dài", "shots": 99})
    tasks.validate_story({"idea": "một ý tưởng đủ dài", "shots": 8})
    with pytest.raises(InvalidInput):
        tasks.validate_images({"project_dir": ""})
    script = story.format_story(story.to_story(_answer(), 6))
    tasks.validate_images({"script": script})  # chưa có dự án nhưng có kịch bản dán sẵn → hợp lệ
    with pytest.raises(InvalidInput):
        tasks.validate_images({"script": "TIÊU ĐỀ: thiếu cảnh"})


def test_images_without_project_creates_one_from_pasted_script(tmp_path, monkeypatch):
    settings = Settings(default_output_dir=str(tmp_path / "out"), min_free_disk_gb=0.0)
    script = "# kịch bản tự viết\n" + story.format_story(story.to_story(_answer(), 6))
    gen = FakeGenerator()
    monkeypatch.setattr(models, "require_lcm", lambda d: Path(d))
    images = tasks.make_images_handler(tmp_path, lambda: settings, lambda d: gen)
    projs = []
    for n in (1, 2):  # dán lần hai → dự án riêng, không ghi đè dự án đầu
        ctx, logs = _ctx({"script": script, "output_dir": str(tmp_path / "mine")}, tmp_path / f"t{n}")
        out = images(ctx)
        proj = Path(out["project_dir"])
        assert proj.parent == tmp_path / "mine" and out["generated"] == 6
        assert project.load(proj)["images"] and story.parse_story(project.read_script(proj)).title == "The Silver Secret"
        assert len(list((proj / project.IMAGES).glob("cảnh *.png"))) == 6
        projs.append(proj)
    assert projs[0].name == "The Silver Secret - phim AI" and projs[1] != projs[0]
    assert sorted((tmp_path / "mine").iterdir()) == sorted(projs)


# ------------------------------------------------------------------ chạy thật 3 bước


def test_full_pipeline_real_ffmpeg(tmp_path, ffmpeg_tools, monkeypatch):
    settings = Settings(default_output_dir=str(tmp_path / "out"), min_free_disk_gb=0.0)
    # bước 1
    llm = FakeLLM(_answer())
    ctx, logs = _ctx({"idea": "a girl with a secret", "shots": 6, "output_dir": str(tmp_path / "out")}, tmp_path / "t1")
    res = tasks.make_story_handler(lambda: settings, lambda s: llm)(ctx)
    proj = Path(res["project_dir"])
    assert proj.name == "The Silver Secret - phim AI" and res["shots"] == 6
    assert (proj / project.SCRIPT_TXT).read_text(encoding="utf-8") == res["script"]

    # bước 2: người dùng sửa kịch bản (đổi lời cảnh 1) rồi vẽ ảnh
    script = res["script"].replace("Line number 0.", "Who are you?")
    gen = FakeGenerator()
    monkeypatch.setattr(models, "require_lcm", lambda d: Path(d))
    images = tasks.make_images_handler(tmp_path, lambda: settings, lambda d: gen)
    ctx, _ = _ctx({"project_dir": str(proj), "script": script}, tmp_path / "t2")
    out = images(ctx)
    assert out["generated"] == 6 and len(gen.prompts) == 6 and "Who are you?" in project.read_script(proj)
    assert sorted(p.name for p in (proj / project.IMAGES).glob("cảnh *.png")) == [f"cảnh {i:02d}.png" for i in range(1, 7)]
    # chạy lại: không vẽ gì; vẽ lại cảnh 3 → chỉ vẽ 1 ảnh với seed khác
    ctx, _ = _ctx({"project_dir": str(proj)}, tmp_path / "t3")
    assert images(ctx)["generated"] == 0
    ctx, _ = _ctx({"project_dir": str(proj), "regenerate": [3]}, tmp_path / "t4")
    assert images(ctx)["generated"] == 1 and gen.prompts[-1][1] != gen.prompts[2][1]

    # bước 3: đổi mô tả ảnh mà chưa vẽ lại → báo lỗi rõ, không dựng
    rend = tasks.make_render_handler(tmp_path, lambda: ffmpeg_tools, lambda: settings)
    changed = script.replace("scene 4", "scene four")
    ctx, _ = _ctx({"project_dir": str(proj), "script": changed}, tmp_path / "t5")
    with pytest.raises(InvalidInput, match="cảnh 5"):
        rend(ctx)
    assert "scene four" in project.read_script(proj)  # bản sửa vẫn được lưu, không mất công sửa

    spoken = []
    monkeypatch.setattr(models, "require_en_voice", lambda d, key: Path(f"{key}.onnx"))
    monkeypatch.setattr(tasks.tts, "load_voice", lambda onnx: onnx.stem)

    def fake_synth(voice, text, tone, speaker, out, ctx=None, on_progress=None):
        spoken.append((voice, speaker, text))
        _tone(out, 1.0 + len(text) / 20)
        return {}

    monkeypatch.setattr(tasks.tts, "synthesize", fake_synth)
    ctx, _ = _ctx({"project_dir": str(proj), "script": script, "show_en": True}, tmp_path / "t6")
    res = rend(ctx)
    assert res["lines"] == 4 and len(spoken) == 4
    assert {v for v, _, _ in spoken} == {"kristin", "john"} and ("kristin", None, "Who are you?") in spoken
    info = probe(Path(res["video"]), ffmpeg_tools)
    assert (info.video[0].width, info.video[0].height) == (720, 1280) and info.audio
    assert info.duration == pytest.approx(res["duration"], abs=0.1) and res["duration"] >= 6 * render.MIN_SHOT - 0.1
    credits = (proj / project.CREDITS_TXT).read_text(encoding="utf-8")
    assert "kristin" in credits and "john" in credits and "LCM" in credits
    assert json.loads((proj / project.MANIFEST).read_text(encoding="utf-8"))["render"]["shots"] == 6
    # dựng lại: giọng đã có trong bộ nhớ đệm → không đọc lại
    ctx, _ = _ctx({"project_dir": str(proj)}, tmp_path / "t7")
    rend(ctx)
    assert len(spoken) == 4
