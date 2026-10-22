"""Làm lại video: logic thuần (kịch bản, giọng đọc, kế hoạch dựng, SEO) với LLM giả lập + kiểm tra tham số,
và chạy thật bước dựng video (FFmpeg) trên video tổng hợp với giọng đọc là file WAV tự sinh."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from pas.core.errors import ExternalToolError, InvalidInput
from pas.jobs.context import JobContext, JobControl
from pas.media.ffmpeg import CREATE_NO_WINDOW, probe
from pas.modules.remake import asr, edit, project, script, seo, tasks, tts
from pas.modules.remake.asr import Line
from pas.services import build_services


class FakeLLM:
    """Trả lần lượt các câu trả lời đã định sẵn cho chat_json; ghi lại yêu cầu."""

    model = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def chat_json(self, user, max_tokens, temperature, ctx=None, system="", schema=None, num_ctx=None):
        self.calls.append({"user": user, "system": system, "temperature": temperature})
        ans = self.answers.pop(0) if self.answers else {}
        return ans(user) if callable(ans) else ans


def _lines(n, vi=True):
    return [Line(i, i * 2.0, i * 2.0 + 1.8, f"第{i}句", f"câu số {i}" if vi else "") for i in range(n)]


# ------------------------------------------------------------------ kịch bản


def test_translate_via_english_fills_and_reports_failures():
    lines = _lines(4, vi=False)
    # Lượt 1 (→ tiếng Anh): bỏ sót câu 3, dịch riêng lại vẫn hỏng (còn chữ Hán) → câu 3 hỏng, không sang lượt 2.
    # Lượt 2 (Anh → Việt): bỏ sót câu 2, dịch riêng lại được.
    llm = FakeLLM({"items": [{"id": i, "en": f"line {i}"} for i in range(3)]},
                  {"items": [{"id": 3, "en": "还是中文"}]},
                  {"items": [{"id": 0, "vi": "một"}, {"id": 1, "vi": "hai"}]},
                  {"items": [{"id": 2, "vi": "ba"}]})
    saved = []
    failed = script.translate(llm, lines, "zh", {"topic": "gemstone cutting",
                                                 "glossary": [{"src": "蓝宝", "en": "sapphire"}]},
                              on_batch=lambda: saved.append([ln.en for ln in lines]))
    assert [ln.en for ln in lines] == ["line 0", "line 1", "line 2", ""]
    assert [ln.vi for ln in lines] == ["một", "hai", "ba", ""]
    assert failed == [3] and saved  # mỗi lô đều báo để lưu tiến độ
    assert "gemstone cutting" in llm.calls[0]["system"] and "蓝宝 (sapphire)" in llm.calls[0]["system"]
    assert "Translate:\n3: 第3句" in llm.calls[1]["user"] and "Previous lines" in llm.calls[1]["user"]
    assert "Translate:\n0: line 0" in llm.calls[2]["user"] and "Vietnamese" in llm.calls[2]["system"]
    assert "- line 1 → hai" in llm.calls[3]["user"]  # ngữ cảnh lượt 2 là bản tiếng Anh + bản Việt đã có
    # Chạy lại: câu đã có bản tiếng Anh thì không dịch lại sang tiếng Anh.
    lines[3].en, again = "line 3", FakeLLM({"items": [{"id": 3, "vi": "bốn"}]})
    assert script.translate(again, lines, "zh", {}) == [] and lines[3].vi == "bốn" and len(again.calls) == 1
    # Nguồn tiếng Anh: bỏ lượt 1.
    en = [Line(0, 0, 1, "hello")]
    assert script.translate(FakeLLM({"items": [{"id": 0, "vi": "xin chào"}]}), en, "en", {}) == []
    assert en[0].vi == "xin chào"
    # Dự án tạo bởi bản cũ lưu bảng thuật ngữ dạng {'src','vi'} → vẫn dùng được.
    assert "蓝宝 = ngọc bích" in script.english_system("zh", "", [{"src": "蓝宝", "vi": "ngọc bích"}])
    assert Line.from_dict(lines[0].to_dict()) == lines[0]


def test_build_context_rejects_chinese_and_invented_terms():
    lines = [Line(0, 0, 1, "这颗粉蓝宝需要重新切割"), Line(1, 1, 2, "亭部太深了")]
    chinese = {"field": "宝石", "summary": "视频讲述了重新切割粉蓝宝。", "glossary": []}
    good = {"field": "gemstone cutting", "summary": "A cutter recuts a pink-blue sapphire.",
            "glossary": [{"src": "粉蓝宝", "meaning": "pink-blue sapphire"},
                         {"src": "亭部", "meaning": "pavilion (lower part of a cut gem)"},
                         {"src": "钻石", "meaning": "diamond"},  # chữ Hán không có trong transcript → bỏ
                         {"src": "girdle", "meaning": "widest edge of a cut gem"},  # thuật ngữ tiếng Anh → giữ
                         {"src": "粉蓝宝", "meaning": "duplicate"},
                         {"src": "切割", "meaning": "切割"}]}  # nghĩa còn chữ Trung → bỏ
    llm = FakeLLM(chinese, good)
    got = script.build_context(llm, lines, "zh")
    assert got["topic"] == "gemstone cutting. A cutter recuts a pink-blue sapphire."
    assert got["glossary"] == [{"src": "粉蓝宝", "en": "pink-blue sapphire"},
                               {"src": "亭部", "en": "pavilion (lower part of a cut gem)"},
                               {"src": "girdle", "en": "widest edge of a cut gem"}]
    assert [c["temperature"] for c in llm.calls] == [0.2, 0.5]
    assert "亭部太深了" in llm.calls[0]["user"] and "Chinese" in llm.calls[0]["user"]
    assert script.build_context(FakeLLM(chinese, chinese), lines, "zh") == {"topic": "", "glossary": []}


def test_translate_vietnamese_source_copies_text():
    lines = [Line(0, 0, 1, "xin chào")]
    assert script.translate(FakeLLM(), lines, "vi", {}) == []
    assert lines[0].vi == "xin chào"


def test_parse_body_clamps_sorts_and_drops_overlap():
    chunk = _lines(10)
    data = {"parts": [{"from": 4, "to": 7, "text": "Đoạn hai rất hay."},
                      {"from": 0, "to": 3, "text": "Đoạn một mở màn."},
                      {"from": 5, "to": 6, "text": "Trùng lặp nằm gọn trong đoạn hai."},
                      {"from": 6, "to": 99, "text": "Đoạn ba vắt qua."},
                      {"from": "x", "to": 2, "text": "sai kiểu"},
                      {"from": 8, "to": 9, "text": "这是中文"}]}
    parts = script.parse_body(data, chunk)
    assert [(a, b) for a, b, _ in parts] == [(0, 3), (4, 7), (8, 9)]
    assert script.coverage(parts, chunk) == 1.0


def test_rewrite_body_retries_then_raises():
    chunk = _lines(10)
    low = {"parts": [{"from": 0, "to": 2, "text": "Chỉ một ít nội dung."}]}
    good = {"parts": [{"from": 0, "to": 9, "text": "Viết lại đầy đủ cả đoạn."}]}
    llm = FakeLLM(low, good)
    assert script.rewrite_body(llm, chunk, "", "story") == [(0, 9, "Viết lại đầy đủ cả đoạn.")]
    assert [c["temperature"] for c in llm.calls] == [0.4, 0.7]
    with pytest.raises(ExternalToolError, match="không viết lại được"):
        script.rewrite_body(FakeLLM(low, low), chunk, "", "story")


def test_write_hook_and_assemble():
    lines = _lines(20)
    llm = FakeLLM({"hook": "这", "cta": "x"},  # hỏng: chữ Trung / quá ngắn → thử lại
                  {"hook": "Viên đá này từng bị cắt hỏng — xem nó lột xác!", "from": 18, "to": 15,
                   "cta": "Theo dõi để xem thêm nhé."})
    hook, src, cta = script.write_hook(llm, lines, "", "short")
    assert src == (15, 18) and hook.startswith("Viên đá")
    parts = script.assemble((hook, src, cta), [(0, 9, "Thân một."), (10, 19, "Thân hai.")])
    assert [p.role for p in parts] == ["hook", "body", "body", "cta"]
    assert [p.id for p in parts] == [0, 1, 2, 3] and parts[1].src == (0, 9)
    assert script.ScriptPart.from_dict(parts[0].to_dict()) == parts[0]
    assert script.script_text(parts).count("\n\n") == 3
    bad = {"hook": "Một câu mở đầu đủ dài.", "from": 99, "to": 1, "cta": "Theo dõi kênh nhé."}
    assert script.write_hook(FakeLLM(bad), lines, "", "short")[1] is None  # id ngoài phạm vi → không gắn cảnh
    # Phần chào đầu video (3 dòng đầu của 20 dòng) không được đưa cho mô hình và không dùng làm cảnh hook.
    intro = {"hook": "Chào mừng các bạn đến với kênh!", "from": 0, "to": 2, "cta": "Theo dõi kênh nhé."}
    llm = FakeLLM(intro)
    assert script.write_hook(llm, lines, "", "short")[1] is None
    assert not llm.calls[0]["user"].startswith("0:") and llm.calls[0]["user"].startswith("3:")
    assert script.intro_lines(20) == 3 and script.intro_lines(104) == 10 and script.intro_lines(5) == 0
    # Lỗi thấy khi chạy thật: hook chép nguyên văn dòng nguồn kèm số thứ tự ("10: … 11: …") → bỏ số, chép thì thử lại.
    copy = {"hook": "10: Câu số 10. 11: câu số 11", "from": 10, "to": 11, "cta": "Theo dõi kênh nhé."}
    fresh = {"hook": "10: Ai ngờ một vết cắt hỏng lại thành 1,5 carat?", "from": 10, "to": 11, "cta": "Theo dõi nhé."}
    hook, src, _ = script.write_hook(FakeLLM(copy, fresh), lines, "", "short")
    assert hook == "Ai ngờ một vết cắt hỏng lại thành 1,5 carat?" and src == (10, 11)


def test_parse_body_strips_copied_line_ids():
    parts = script.parse_body({"parts": [{"from": 0, "to": 4, "text": "0: Mở đầu. 3: tiếp, 200: giữ nguyên"}]},
                              _lines(5))
    assert parts == [(0, 4, "Mở đầu. tiếp, 200: giữ nguyên")]


# ------------------------------------------------------------------ giọng đọc


def test_paragraphs_and_sentences():
    text = "Đoạn một. Câu hai!\n\n  Đoạn   hai 🙂 có emoji?  \n\n\n"
    paras = tts.paragraphs(text)
    assert paras == ["Đoạn một. Câu hai!", "Đoạn hai có emoji?"]
    assert tts.sentences(paras[0]) == ["Đoạn một.", "Câu hai!"]
    assert tts.paragraphs("dòng 1\ndòng 2") == ["dòng 1", "dòng 2"]  # không có dòng trống: mỗi dòng một đoạn
    with pytest.raises(InvalidInput):
        tts.validate_text("  🙂 \n ")
    with pytest.raises(InvalidInput, match="quá dài"):
        tts.validate_text("a" * (tts.MAX_CHARS + 1))


def test_match_script_by_content_then_order():
    script_parts = [{"id": 0, "text": "Mở đầu."}, {"id": 1, "text": "Thân bài."}, {"id": 2, "text": "Kết."}]
    segs = [{"text": "mở  đầu"}, {"text": "Thân bài đã sửa"}, {"text": "Kết."}]
    tts.match_script(segs, script_parts)
    assert [s["script_id"] for s in segs] == [0, 1, 2]
    segs = [{"text": "Mới hoàn toàn"}, {"text": "Kết."}]
    tts.match_script(segs, script_parts)
    assert [s["script_id"] for s in segs] == [None, 2]


def test_read_wav_requires_16k_mono(tmp_path):
    good, bad = tmp_path / "a.wav", tmp_path / "b.wav"
    for path, rate in ((good, 16000), (bad, 44100)):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
            w.writeframes(struct.pack("<3h", 0, 16384, -32768))
    assert asr.read_wav(good).tolist() == [0.0, 0.5, -1.0]
    with pytest.raises(ExternalToolError):
        asr.read_wav(bad)


# ------------------------------------------------------------------ kế hoạch dựng


def test_detect_cuts_and_shots():
    diffs = [0, 2, 3, 2, 60, 2, 2, 3, 2, 2, 2, 55, 2, 2] + [2] * 20
    cuts = edit.detect_cuts(diffs, fps=4)
    assert cuts == [1.0, 2.75]
    assert edit.shots_from_cuts([1.0, 1.2, 2.75], 8.5) == [(0.0, 1.2), (1.2, 2.75), (2.75, 8.5)]


def test_pick_pieces_sum_and_no_reuse():
    shots = [(i * 4.0, i * 4.0 + 4.0) for i in range(25)]  # 100 s, cảnh dài 4 s
    used: list = []
    a = edit.pick_pieces(shots, 10, 30, 9.0, 100, used)
    assert math.isclose(sum(d for _, d in a), 9.0, abs_tol=2e-3)
    assert all(edit.MIN_PIECE * 0.5 <= d <= edit.MAX_PIECE + 1e-6 for _, d in a)
    assert all(10 - 1e-6 <= s and s + d <= 30 + 1e-6 for s, d in a)
    b = edit.pick_pieces(shots, 10, 30, 6.0, 100, used)
    for s1, d1 in a:
        for s2, d2 in b:
            assert min(s1 + d1, s2 + d2) - max(s1, s2) <= 1e-6  # không dùng lại cùng hình


def test_pick_pieces_widens_short_range():
    shots = [(0.0, 60.0)]
    out = edit.pick_pieces(shots, 20, 21, 8.0, 60, [])
    assert math.isclose(sum(d for _, d in out), 8.0, abs_tol=2e-3)  # làm tròn ms; plan() gióng lại theo khung
    assert all(0 <= s and s + d <= 60 for s, d in out)


def test_plan_covers_voice_and_puts_hook_scene_first():
    lines = [{"id": i, "start": i * 3.0, "end": i * 3.0 + 2.5} for i in range(40)]  # 120 s
    shots = [(i * 5.0, i * 5.0 + 5.0) for i in range(24)]
    parts = [{"id": 0, "src": [35, 38]}, {"id": 1, "src": [0, 19]}, {"id": 2, "src": [20, 39]}, {"id": 3, "src": None}]
    segs = [{"start": 0.0, "end": 4.0, "script_id": 0}, {"start": 4.4, "end": 20.0, "script_id": 1},
            {"start": 20.3, "end": 35.0, "script_id": 2}, {"start": 35.3, "end": 38.0, "script_id": 3}]
    pieces = edit.plan(segs, parts, lines, shots, 120.0, 38.5)
    assert pieces[0].src_start >= 100  # hook: cảnh cuối video lên đầu
    t = 0.0
    for p in pieces:
        assert math.isclose(p.out_start, t, abs_tol=1e-6)
        end = (p.out_start + p.duration) * edit.FPS
        assert abs(end - round(end)) < 1e-3  # kết thúc đúng ranh giới khung
        t += p.duration
    assert math.isclose(t, 38.5, abs_tol=1 / edit.FPS)
    assert {p.zoom for p in pieces} == set(edit.ZOOMS)
    assert all(p.zoom == 1.0 for p in edit.plan(segs, parts, lines, shots, 120.0, 38.5, "none"))


def test_piece_filter_blurs_subtitle_starting_right_after_piece():
    from pas.modules.vietsub.ocr import SubEvent

    ev = SubEvent(10.0, 12.25, "两头呢是挺漏底的啊", (477.0, 591.0, 804.0, 633.0), 42.0, False, None)
    f = edit.piece_filter(edit.Piece(0, 8.0, 2.0, 1.0, 0.0), [ev], 1280, 720, "original", "crop")
    assert "between(t,1.700,2.000)" in f
    logo = SubEvent(0.0, 300.0, "抖音", (10.0, 10.0, 90.0, 40.0), 30.0, True, None)
    assert "boxblur" not in edit.piece_filter(edit.Piece(0, 8.0, 2.0, 1.0, 0.0), [logo], 1280, 720, "original", "crop")


def test_split_caption_and_events():
    assert edit.split_caption("một hai ba bốn", 100) == ["một hai ba bốn"]
    parts = edit.split_caption("Viên sapphire hồng này, sau khi cắt lại, trở nên rực rỡ hơn hẳn trước đây", 32)
    assert all(len(p) <= 32 for p in parts) and parts[0].endswith(",")
    segs = [{"sentences": [{"text": "Câu ngắn.", "start": 1.0, "end": 2.0},
                           {"text": " ".join(["chữ"] * 30), "start": 2.1, "end": 8.1}]}]
    ev = edit.caption_events(segs, "9:16")
    assert ev[0] == (1.0, 2.0, "Câu ngắn.") and len(ev) > 2
    assert math.isclose(ev[-1][1], 8.1, abs_tol=1e-3)
    ass = edit.build_ass(ev, (1080, 1920), "9:16", "Tiêu đề", 3.0, "Nguồn: tác giả")
    assert "Style: Cap" in ass and ",Title,," in ass and ",Credit,," in ass and "Câu ngắn." in ass


# ------------------------------------------------------------------ SEO


def _seo_answer(**over):
    data = {"titles": ["Viên đá cắt hỏng được cứu thế nào?", "Thợ cắt đá quý sửa sai", "Sapphire hồng lột xác",
                       "这是中文标题不能要"],
            "thumbnail_texts": ["CỨU VIÊN ĐÁ HỎNG", "TRƯỚC VÀ SAU"],
            "description": "Xem thợ cắt lại viên sapphire hồng.",
            "keywords": ["cắt đá quý", "sapphire hồng", "mài đá", "đá quý", "cắt lại đá", "cắt đá quý"],
            "hashtags": ["#đáquý", "sapphire hồng", "#DaQuy"], "audience": "Người thích đá quý."}
    data.update(over)
    return data


def test_parse_seo_filters_and_requires_minimum():
    res = seo.parse_seo(_seo_answer())
    assert len(res.titles) == 3 and all("中" not in t for t in res.titles)
    assert res.keywords.count("cắt đá quý") == 1 and len(res.keywords) == 5
    assert res.hashtags == ["#đáquý", "#sapphirehồng", "#DaQuy"]
    assert seo.parse_seo(_seo_answer(keywords=["a1", "b2"])) is None
    assert seo.parse_seo(_seo_answer(thumbnail_texts=[])).thumbnail_texts  # thiếu → lấy từ tiêu đề
    text = seo.seo_text(res, "Kênh gốc", "Giọng đọc: Piper")
    assert "Nguồn video gốc: Kênh gốc" in text and "KHÔNG có số liệu" in text and "bản quyền" in text.lower()


def test_generate_seo_retries_then_raises():
    llm = FakeLLM({}, _seo_answer())
    assert seo.generate_seo(llm, "kịch bản").titles
    with pytest.raises(ExternalToolError):
        seo.generate_seo(FakeLLM({}, {}), "kịch bản")


def test_pick_times_spread():
    scores = [(float(t), 100.0 - abs(t - 50)) for t in range(0, 101)]
    times = seo.pick_times(scores, 3, 15.0)
    assert len(times) == 3 and all(b - a >= 15 for a, b in zip(times, times[1:]))
    assert seo.pick_times([(0.0, 1.0), (1.0, 2.0)], 3, 15.0) == [0.0, 1.0]
    assert seo.pick_times([]) == []


def test_render_thumbnail(tmp_path):
    from PIL import Image

    frame = tmp_path / "f.png"
    Image.new("RGB", (640, 480), (40, 120, 200)).save(frame)
    out = tmp_path / "t.jpg"
    seo.render_thumbnail(frame, "VIÊN ĐÁ HỒNG LỘT XÁC", out, seo.find_font(), [(10, 400, 600, 470)])
    with Image.open(out) as im:
        assert im.size == seo.THUMB_SIZE
        assert im.convert("L").getextrema()[1] > 200  # có chữ sáng màu


# ------------------------------------------------------------------ kiểm tra tham số


def test_validate_params(tmp_path):
    with pytest.raises(InvalidInput):
        tasks.validate_script({})
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(InvalidInput, match="không phải video"):
        tasks.validate_script({"path": str(txt)})
    with pytest.raises(InvalidInput, match="Chưa có dự án"):
        tasks.validate_voice({"text": "Xin chào.", "voice": "vais1000"})
    proj = tmp_path / "p - làm lại"
    proj.mkdir()
    project.save(proj, {"version": 1, "source": str(txt)})
    with pytest.raises(InvalidInput, match="Giọng đọc"):
        tasks.validate_voice({"project_dir": str(proj), "text": "Xin chào.", "voice": "nope"})
    with pytest.raises(InvalidInput, match="Người đọc"):
        tasks.validate_voice({"project_dir": str(proj), "text": "Xin chào.", "voice": "vais1000", "speaker": 3})
    tasks.validate_voice({"project_dir": str(proj), "text": "Xin chào.", "voice": "vivos", "speaker": 64})
    with pytest.raises(InvalidInput, match="chưa có giọng đọc"):
        tasks.validate_edit({"project_dir": str(proj), "aspect": "9:16", "fit": "blur"})


def test_tasks_registered(tmp_path):
    sv = build_services(tmp_path / "data", start_jobs=False)
    try:
        kinds = sv.registry.kinds()
        assert {tasks.KIND_SCRIPT, tasks.KIND_VOICE, tasks.KIND_EDIT, tasks.KIND_SEO} <= set(kinds)
        sv.registry.get("model.download").validate({"model": "whisper-large-v3-turbo"})
        sv.registry.get("model.download").validate({"model": "piper-vi_VN-vais1000-medium"})
    finally:
        sv.close()


# ------------------------------------------------------------------ chạy thật bước dựng (FFmpeg)


def _ctx(params, tmp):
    logs = []
    tmp.mkdir(parents=True, exist_ok=True)
    return JobContext(1, params, tmp, JobControl(), lambda p, m: None, lambda lv, m: logs.append((lv, m))), logs


def _write_tone(path, seconds, rate=22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(seconds * rate))))


def test_edit_handler_real_ffmpeg(tmp_path, ffmpeg_tools):
    src = tmp_path / "nguồn.mp4"
    # 3 cảnh màu khác nhau, mỗi cảnh 4 s, có tiếng
    subprocess.run([str(ffmpeg_tools.ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=red:s=640x360:r=30:d=4",
                    "-f", "lavfi", "-i", "color=green:s=640x360:r=30:d=4", "-f", "lavfi",
                    "-i", "color=blue:s=640x360:r=30:d=4", "-f", "lavfi", "-i", "sine=f=300:d=12",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)],
                   check=True, creationflags=CREATE_NO_WINDOW)
    proj = tmp_path / "nguồn - làm lại"
    proj.mkdir()
    lines = [{"id": i, "start": i * 2.0, "end": i * 2.0 + 1.8, "text": "x", "vi": "x"} for i in range(6)]
    project.save(proj, {"version": 1, "source": str(src), "transcript": {"language": "zh", "lines": lines},
                        "script": [{"id": 0, "role": "hook", "text": "Mở đầu.", "src": [5, 5]},
                                   {"id": 1, "role": "body", "text": "Thân.", "src": [0, 4]}]})
    _write_tone(proj / project.VOICE_WAV, 7.0)
    segs = [{"index": 0, "text": "Mở đầu.", "start": 0.0, "end": 2.0, "script_id": 0,
             "sentences": [{"text": "Mở đầu.", "start": 0.0, "end": 2.0}]},
            {"index": 1, "text": "Thân.", "start": 2.3, "end": 6.6, "script_id": 1,
             "sentences": [{"text": "Thân bài nói về viên đá.", "start": 2.3, "end": 6.6}]}]
    (proj / project.VOICE_JSON).write_text(json.dumps({"segments": segs, "duration": 7.0}), encoding="utf-8")
    sv = build_services(tmp_path / "data", start_jobs=False)
    try:
        params = {"project_dir": str(proj), "aspect": "9:16", "fit": "blur", "zoom": "alternate", "captions": True,
                  "blur_subs": False, "bg_volume": 0.2, "title": "Tiêu đề thử", "credit": "Nguồn: thử"}
        spec = sv.registry.get(tasks.KIND_EDIT)
        spec.validate(params)
        ctx, logs = _ctx(params, tmp_path / "job")
        res = spec.handler(ctx)
    finally:
        sv.close()
    out = Path(res["video"])
    info = probe(out, ffmpeg_tools)
    assert (info.video[0].width, info.video[0].height) == (1080, 1920)
    assert abs(info.duration - 7.0) < 0.2 and info.has_audio
    # cảnh đầu lấy từ dòng 5 (giây 10–12) = cảnh xanh dương → hook đổi thứ tự
    frame = subprocess.run([str(ffmpeg_tools.ffmpeg), "-v", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                            "-vf", "crop=200:200:440:860,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True, creationflags=CREATE_NO_WINDOW).stdout
    r, g, b = frame[:3]
    assert b > 150 and r < 80 and g < 80, (r, g, b)
    assert src.stat().st_size > 0 and not list(proj.glob("*.part"))
    assert project.load(proj)["edit"]["pieces"] == res["pieces"]
