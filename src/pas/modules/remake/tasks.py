"""4 tác vụ của "Làm lại video" — mỗi bước đọc/ghi chung thư mục dự án; chạy lại thì dùng lại kết quả đã có.

- remake.script: video → transcript gốc (Whisper) → bản dịch theo ngữ cảnh → kịch bản lời kể mới (hook/thân/kết).
- remake.voice:  kịch bản (người dùng có thể sửa) → giọng đọc WAV + mốc thời gian từng câu (Piper hoặc VieNeu).
- remake.edit:   video gốc + giọng đọc → video mới (cắt ngắn, đổi thứ tự, zoom/crop, chữ, khớp giọng).
- remake.seo:    kịch bản → tiêu đề/mô tả/từ khóa/hashtag + 3 thumbnail.
Video gốc chỉ được đọc. Mọi file ghi ra nằm trong thư mục "<tên video> - làm lại".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from pas.core.config import Settings
from pas.core.disk import GB, ensure_free_space
from pas.core.errors import ExternalToolError, InvalidInput
from pas.core.paths import AppPaths, ensure_not_source
from pas.jobs.context import JobContext
from pas.jobs.registry import TaskRegistry, TaskSpec
from pas.media.ffmpeg import FFmpegTools, MediaInfo, probe, run_ffmpeg
from pas.modules.clips.tasks import ASPECTS, FITS, VIDEO_EXTENSIONS
from pas.modules.remake import asr, edit, models, project, script, seo, tts, vieneu_tts
from pas.modules.vietsub import ocr as vs_ocr
from pas.modules.vietsub import render as vs_render
from pas.modules.vietsub.translate import OllamaClient

log = logging.getLogger(__name__)

KIND_SCRIPT = "remake.script"
KIND_VOICE = "remake.voice"
KIND_EDIT = "remake.edit"
KIND_SEO = "remake.seo"
DURATION_TOLERANCE = 0.5
MAX_TITLE = 80
MAX_CREDIT = 120
OCR_VERSION = 2  # đổi khi cách quét chữ cũ thay đổi → dự án cũ tự quét lại ở lần dựng sau


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _srt(lines: list[asr.Line], field: str) -> str:
    blocks = []
    for i, ln in enumerate((ln for ln in lines if getattr(ln, field)), 1):
        blocks.append(f"{i}\n{vs_render._srt_time(ln.start)} --> {vs_render._srt_time(ln.end)}\n{getattr(ln, field)}\n")
    return "\n".join(blocks)


def _probe_source(src: Path, tools: FFmpegTools, need_audio: bool) -> MediaInfo:
    info = probe(src, tools)
    if not info.has_video:
        raise InvalidInput(f"'{src.name}' không có hình ảnh.", "Chọn một file video.")
    if info.duration <= 0:
        raise InvalidInput(f"Không xác định được thời lượng của '{src.name}'.", "File có thể ghi dở hoặc hỏng.")
    if need_audio and not info.has_audio:
        raise InvalidInput(f"'{src.name}' không có âm thanh — không lấy được lời thoại.",
                           "Chọn video có lời nói/lời kể.")
    return info


def _client(settings: Settings) -> OllamaClient:
    client = OllamaClient(settings.ollama_url, settings.translate_model)
    client.ensure_ready()
    return client


# ================================================================ bước 1: lấy SUB & viết lại


def validate_script(params: dict) -> None:
    raw = params.get("path")
    if not raw:
        raise InvalidInput("Chưa chọn video.", "Bấm 'Chọn…' hoặc kéo thả video vào ô.")
    path = Path(raw)
    if not path.is_file():
        raise InvalidInput(f"Không tìm thấy file: {path}", "Kiểm tra lại đường dẫn.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise InvalidInput(f"Định dạng '{path.suffix or '(không có đuôi)'}' không phải video được hỗ trợ.",
                           "Hỗ trợ: " + ", ".join(sorted(e.lstrip('.').upper() for e in VIDEO_EXTENSIONS)))
    if params.get("language", "auto") not in asr.LANGUAGES:
        raise InvalidInput(f"Ngôn ngữ không hợp lệ: {params.get('language')!r}")
    if params.get("style", "story") not in script.STYLES:
        raise InvalidInput(f"Văn phong không hợp lệ: {params.get('style')!r}")
    out = params.get("output_dir")
    if out and Path(out).exists() and not Path(out).is_dir():
        raise InvalidInput(f"Thư mục đầu ra không phải thư mục: {out}")


def make_script_handler(tools_provider: Callable[[], FFmpegTools], settings_provider: Callable[[], Settings],
                        models_dir: Path):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        src = Path(p["path"])
        language, style = p.get("language", "auto"), p.get("style", "story")
        settings = settings_provider()
        tools = tools_provider()

        ctx.report(0.01, "Đang đọc thông tin video")
        info = _probe_source(src, tools, need_audio=True)
        model_dir = models.require_whisper(models_dir)
        client = _client(settings)  # kiểm tra Ollama trước khi tốn vài phút nhận dạng giọng nói
        ctx.checkpoint()

        state_file = ctx.temp_dir / "remake_state.json"
        state = _load_json(state_file)
        proj = Path(state["project_dir"]) if state.get("project_dir") else None
        if proj is None or not project.manifest_path(proj).is_file():
            base = Path(p.get("output_dir") or settings.default_output_dir or src.parent)
            proj = project.new_project_dir(src, base)
            ensure_free_space(proj.parent if proj.parent.exists() else base, int(settings.min_free_disk_gb * GB))
            proj.mkdir(parents=True)
            data = {"version": project.VERSION, "source": str(src), "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source_duration": info.duration}
            project.save(proj, data)
            _save_json(state_file, {"project_dir": str(proj)})
            ctx.log(f"Dự án mới: {proj}")
        else:
            data = project.load(proj)
            ctx.log(f"Chạy tiếp dự án {proj}")

        # --- transcript
        if "transcript" in data:
            tr = data["transcript"]
            lines = [asr.Line.from_dict(d) for d in tr["lines"]]
            ctx.log(f"Dùng lại transcript từ lần chạy trước ({len(lines)} câu)")
        else:
            ctx.report(0.02, "Đang tách âm thanh")
            wav = asr.extract_audio(src, tools, ctx.temp_dir / "audio16k.wav", info.duration, ctx,
                                    ctx.sub_progress(0.02, 0.05))
            ctx.report(0.05, "Đang nạp mô hình Whisper large-v3-turbo")
            whisper = asr.load_model(model_dir)
            ctx.report(0.08, "Đang nhận dạng lời nói (Whisper, CPU — khoảng 1/3 thời lượng video)")
            lines, lang, prob = asr.transcribe(whisper, wav, info.duration, language, ctx, ctx.sub_progress(0.08, 0.45))
            del whisper
            if not lines:
                raise InvalidInput("Không nghe thấy lời nói nào trong video.",
                                   "Video chỉ có nhạc/tiếng động thì không lấy được transcript.")
            tr = {"language": lang, "probability": round(prob, 3), "lines": [ln.to_dict() for ln in lines]}
            data["transcript"] = tr
            project.save(proj, data)
            ctx.log(f"Transcript: {len(lines)} câu, ngôn ngữ {script.lang_name(lang)} (tin cậy {prob:.0%})")
        lang = tr["language"]
        tpath = proj / project.TRANSCRIPT_SRT
        vs_render.write_text_atomic(tpath, _srt(lines, "text"))

        # --- ngữ cảnh + dịch
        if "context" not in data:
            ctx.report(0.45, f"Đang đọc hiểu nội dung video ({client.model})")
            data["context"] = script.build_context(client, lines, lang, ctx)
            project.save(proj, data)
            if data["context"]["topic"]:
                ctx.log(f"Chủ đề: {data['context']['topic']}")
            else:
                ctx.log("LLM không tóm tắt được chủ đề — dịch không kèm ngữ cảnh chủ đề", "WARNING")
        context = data["context"]
        done_vi = {int(k): v for k, v in (data.get("translation") or {}).items()}
        done_en = {int(k): v for k, v in (data.get("translation_en") or {}).items()}
        for ln in lines:
            ln.vi, ln.en = done_vi.get(ln.id, ""), done_en.get(ln.id, "")
        if lang != "vi" and any(not ln.vi for ln in lines):
            ctx.report(0.5, f"Đang dịch {len(lines)} câu sang tiếng Việt theo ngữ cảnh ({client.model})")

        def _save_batch() -> None:
            data["translation"] = {str(ln.id): ln.vi for ln in lines if ln.vi}
            data["translation_en"] = {str(ln.id): ln.en for ln in lines if ln.en}
            project.save(proj, data)

        failed = script.translate(client, lines, lang, context, ctx, _save_batch, ctx.sub_progress(0.5, 0.75))
        _save_batch()
        if len(failed) > script.MAX_FAILED_RATIO * len(lines):
            raise ExternalToolError(f"Không dịch được {len(failed)}/{len(lines)} câu.",
                                    "Bấm 'Chạy lại' (câu đã dịch được giữ). Nếu lặp lại, chọn mô hình lớn hơn.")
        if failed:
            ctx.log(f"{len(failed)} câu không dịch được (bỏ qua khi viết lại): "
                    + "; ".join(lines[i].text for i in failed[:10]), "WARNING")
        vpath = proj / project.TRANSLATION_SRT
        vs_render.write_text_atomic(vpath, _srt(lines, "vi"))

        # --- viết lại (key theo văn phong: đổi văn phong → viết lại)
        rw = data.get("rewrite") if (data.get("rewrite") or {}).get("style") == style else None
        rw = rw or {"style": style, "body": {}}
        usable = [ln for ln in lines if ln.vi]
        parts_by_chunk = script.chunks(usable)
        for ci, chunk in enumerate(parts_by_chunk):
            if str(ci) in rw["body"]:
                continue
            ctx.checkpoint()
            ctx.report(0.75 + 0.2 * ci / max(1, len(parts_by_chunk)),
                       f"Đang viết lại lời kể ({ci + 1}/{len(parts_by_chunk)})")
            rw["body"][str(ci)] = [list(x) for x in script.rewrite_body(client, chunk, context["topic"], style, ctx)]
            data["rewrite"] = rw
            project.save(proj, data)
        if "hook" not in rw:
            ctx.report(0.95, "Đang viết câu mở đầu (hook) và lời kết")
            hook, src_range, cta = script.write_hook(client, usable, context["topic"], style, ctx)
            rw["hook"] = {"text": hook, "src": list(src_range) if src_range else None, "cta": cta}
            data["rewrite"] = rw
            project.save(proj, data)
        body = [tuple(x) for ci in range(len(parts_by_chunk)) for x in rw["body"][str(ci)]]
        h = rw["hook"]
        parts = script.assemble((h["text"], tuple(h["src"]) if h["src"] else None, h["cta"]), body)
        data["script"] = [x.to_dict() for x in parts]
        project.save(proj, data)
        text = script.script_text(parts)
        spath = proj / project.SCRIPT_TXT
        vs_render.write_text_atomic(spath, text)
        src_chars = sum(len(ln.vi) for ln in usable)
        ctx.log(f"Kịch bản: {len(parts)} đoạn, {len(text)} ký tự (bản dịch {src_chars} ký tự)"
                + (f"; hook dùng cảnh dòng {h['src'][0]}–{h['src'][1]}" if h["src"] else ""))
        ctx.report(1.0, "Đã có kịch bản mới")
        return {"project_dir": str(proj), "source": str(src), "language": lang,
                "language_probability": tr["probability"], "lines": len(lines), "translated": len(usable),
                "failed": [lines[i].text for i in failed], "topic": context["topic"],
                "parts": len(parts), "script_text": text, "model": client.model,
                "files": [str(tpath), str(vpath), str(spath)]}

    return run


# ================================================================ bước 2: giọng đọc


def validate_voice(params: dict) -> None:
    project.validate_project_dir(params)
    tts.validate_text(params.get("text"))
    key = params.get("voice")
    if params.get("tone", "natural") not in tts.TONES:
        raise InvalidInput(f"Tông giọng không hợp lệ: {params.get('tone')!r}")
    if key == vieneu_tts.KEY:
        vieneu_tts.validate_choice(params.get("preset"), params.get("sample"))
        return
    if key not in models.VOICES:
        raise InvalidInput(f"Giọng đọc không hợp lệ: {key!r}")
    sp = params.get("speaker", 0)
    if not isinstance(sp, int) or isinstance(sp, bool) or not 0 <= sp < models.VOICES[key].speakers:
        raise InvalidInput(f"Người đọc phải từ 0 đến {models.VOICES[key].speakers - 1}.")


def voice_credit(key: str) -> str:
    v = models.VOICES[key]
    return f"Giọng đọc: Piper {v.pack.key.removeprefix('piper-')} ({v.pack.license})"


def _speak_vieneu(ctx: JobContext, p: dict, models_dir: Path, tools: FFmpegTools, tone: tts.Tone,
                  out: Path) -> tuple[dict, dict]:
    root = models.require_vieneu(models_dir)
    preset = None if p.get("sample") else p["preset"]
    sample = None
    if p.get("sample"):
        src = Path(p["sample"])
        ctx.report(0.01, "Đang đọc file giọng mẫu")
        sample = ctx.temp_dir / "voice_sample.wav"
        used = vieneu_tts.prepare_sample(src, tools, sample, ctx)
        ctx.log(f"Giọng mẫu: {src.name} (dùng {used:.1f} giây đầu). {vieneu_tts.CLONE_NOTE}")
    ctx.report(0.03, "Đang nạp mô hình VieNeu")
    engine = vieneu_tts.load(root)
    ctx.report(0.08, f"Đang đọc kịch bản ({'giọng nhân bản' if sample else preset}, {tone.label})")
    res = vieneu_tts.synthesize(engine, p["text"], tone, out, ctx, ctx.sub_progress(0.08, 0.97),
                                preset=preset, sample=sample)
    info = {"voice": vieneu_tts.KEY, "speaker": None, "preset": preset,
            "sample": Path(p["sample"]).name if sample else None,
            "license": models.VIENEU.license, "note": models.VIENEU.note,
            "credit": vieneu_tts.credit(preset, sample)}
    return info, res


def _speak_piper(ctx: JobContext, p: dict, models_dir: Path, tone: tts.Tone, out: Path) -> tuple[dict, dict]:
    key = p["voice"]
    voice_info = models.VOICES[key]
    onnx = models.require_voice(models_dir, key)
    ctx.report(0.02, f"Đang nạp giọng {key}")
    voice = tts.load_voice(onnx)
    speaker = p.get("speaker", 0) if voice_info.speakers > 1 else None
    ctx.report(0.05, f"Đang đọc kịch bản ({tone.label})")
    res = tts.synthesize(voice, p["text"], tone, speaker, out, ctx, ctx.sub_progress(0.05, 0.97))
    info = {"voice": key, "speaker": speaker, "preset": None, "sample": None,
            "license": voice_info.pack.license, "note": voice_info.pack.note, "credit": voice_credit(key)}
    return info, res


def make_voice_handler(models_dir: Path, tools_provider: Callable[[], FFmpegTools]):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        proj = project.validate_project_dir(p)
        data = project.load(proj)
        tone_key = p.get("tone", "natural")
        tone = tts.TONES[tone_key]
        ensure_free_space(proj, 200 * 1024 * 1024)
        out = proj / project.VOICE_WAV
        if p["voice"] == vieneu_tts.KEY:
            info, res = _speak_vieneu(ctx, p, models_dir, tools_provider(), tone, out)
        else:
            info, res = _speak_piper(ctx, p, models_dir, tone, out)
        tts.match_script(res["segments"], data.get("script") or [])
        matched = sum(s["script_id"] is not None for s in res["segments"])
        meta = {"voice": info["voice"], "speaker": info["speaker"], "preset": info["preset"], "sample": info["sample"],
                "tone": tone_key, "license": info["license"], "credit": info["credit"], **res}
        vs_render.write_text_atomic(proj / project.VOICE_JSON, json.dumps(meta, ensure_ascii=False, indent=1))
        data["voice"] = {k: meta[k] for k in ("voice", "speaker", "preset", "sample", "tone", "license", "credit",
                                              "duration")}
        project.save(proj, data)
        ctx.log(f"Giọng đọc {res['duration']:.1f}s, {len(res['segments'])} đoạn "
                f"({matched} đoạn khớp kịch bản bước 1 → chọn đúng cảnh)")
        ctx.log(f"Giấy phép: {info['license']}. {info['note']}")
        ctx.report(1.0, "Đã xuất giọng đọc")
        return {"project_dir": str(proj), "wav": str(out), "duration": res["duration"],
                "segments": len(res["segments"]), "matched": matched, "voice": info["voice"], "tone": tone_key,
                "speaker": info["speaker"], "preset": info["preset"], "sample": info["sample"],
                "license": info["license"], "note": info["note"]}

    return run


# ================================================================ bước 3: dựng video


def validate_edit(params: dict) -> None:
    proj = project.validate_project_dir(params)
    if not (proj / project.VOICE_JSON).is_file() or not (proj / project.VOICE_WAV).is_file():
        raise InvalidInput("Dự án chưa có giọng đọc.", "Chạy bước 2 'Tạo giọng đọc' trước.")
    if params.get("aspect") not in ASPECTS:
        raise InvalidInput(f"Khung hình không hợp lệ: {params.get('aspect')!r}")
    if params.get("fit") not in FITS:
        raise InvalidInput(f"Cách lấp khung không hợp lệ: {params.get('fit')!r}")
    if params.get("zoom", "alternate") not in edit.ZOOM_LABELS:
        raise InvalidInput(f"Kiểu zoom không hợp lệ: {params.get('zoom')!r}")
    for k in ("captions", "blur_subs"):
        if not isinstance(params.get(k, True), bool):
            raise InvalidInput(f"Tùy chọn {k} không hợp lệ.")
    vol = params.get("bg_volume", 0.0)
    if not isinstance(vol, (int, float)) or isinstance(vol, bool) or not 0 <= vol <= 1:
        raise InvalidInput("Âm lượng tiếng gốc phải từ 0 đến 100%.")
    for k, limit in (("title", MAX_TITLE), ("credit", MAX_CREDIT)):
        if not isinstance(params.get(k, ""), str) or len(params.get(k, "")) > limit:
            raise InvalidInput(f"'{k}' tối đa {limit} ký tự.")


def verify_video(path: Path, tools: FFmpegTools, size: tuple[int, int], duration: float) -> MediaInfo:
    try:
        info = probe(path, tools)
    except InvalidInput as exc:
        raise ExternalToolError(f"Video xuất ra không đọc được: {exc.message}", "Bấm Chạy lại.") from exc
    problems = []
    if not info.video or info.video[0].codec != "h264":
        problems.append("không có luồng video H.264")
    elif (info.video[0].width, info.video[0].height) != tuple(size):
        problems.append(f"kích thước {info.video[0].width}×{info.video[0].height}, cần {size[0]}×{size[1]}")
    if not any(a.codec == "aac" for a in info.audio):
        problems.append("thiếu âm thanh AAC")
    if abs(info.duration - duration) > DURATION_TOLERANCE:
        problems.append(f"thời lượng {info.duration:.2f}s, cần {duration:.2f}s")
    if problems:
        raise ExternalToolError(f"Video mới không đạt kiểm tra: {'; '.join(problems)}.", "Xem log tác vụ.")
    return info


def make_edit_handler(tools_provider: Callable[[], FFmpegTools], settings_provider: Callable[[], Settings]):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        proj = project.validate_project_dir(p)
        data = project.load(proj)
        src = project.source_of(data)
        settings = settings_provider()
        tools = tools_provider()
        aspect, fit, zoom = p["aspect"], p["fit"], p.get("zoom", "alternate")
        captions, blur_subs = p.get("captions", True), p.get("blur_subs", True)
        bg_volume = float(p.get("bg_volume", 0.0))
        title, credit = p.get("title", "").strip(), p.get("credit", "").strip()

        ctx.report(0.01, "Đang đọc thông tin video")
        info = _probe_source(src, tools, need_audio=False)
        if bg_volume > 0 and not info.has_audio:
            ctx.log("Video gốc không có âm thanh — bỏ qua tiếng nền", "WARNING")
            bg_volume = 0.0
        voice_path = proj / project.VOICE_WAV
        vmeta = _load_json(proj / project.VOICE_JSON)
        segments = vmeta.get("segments") or []
        if not segments:
            raise InvalidInput("voice.json không có đoạn nào.", "Chạy lại bước 2 'Tạo giọng đọc'.")
        voice_duration = probe(voice_path, tools).duration
        if voice_duration <= 0:
            raise InvalidInput("File giọng đọc rỗng hoặc hỏng.", "Chạy lại bước 2.")
        v = info.video[0]
        size = edit.output_size(aspect, v.width, v.height)
        ensure_free_space(proj, int(voice_duration * 2.5e6) + int(settings.min_free_disk_gb * GB))

        # --- phân tích video gốc (lưu vào dự án: lần dựng sau không phải làm lại)
        if "shots" not in data:
            ctx.report(0.02, "Đang tìm điểm chuyển cảnh")
            data["shots"] = edit.detect_shots(src, tools, info.duration, ctx, ctx.sub_progress(0.02, 0.1))
            project.save(proj, data)
        shots = [tuple(s) for s in data["shots"]]
        events: list[vs_ocr.SubEvent] = []
        if blur_subs:
            if "ocr_events" not in data or data.get("ocr_version") != OCR_VERSION:
                ctx.report(0.1, "Đang tìm phụ đề tiếng Trung cũ trên hình (để làm mờ)")
                found = vs_ocr.scan_video(src, tools, v.width, v.height, info.duration, "bottom", ctx,
                                          ctx.sub_progress(0.1, 0.3), min_frames=1)
                data["ocr_events"] = [e.to_dict() for e in found]
                data["ocr_version"] = OCR_VERSION
                project.save(proj, data)
            events = [vs_ocr.SubEvent.from_dict(d) for d in data["ocr_events"]]
            static = sum(e.static for e in events)
            ctx.log(f"Làm mờ {len(events) - static} câu chữ cũ"
                    + (f"; giữ nguyên {static} logo/watermark đứng yên (ghi nguồn tác giả gốc)" if static else ""))

        lines = (data.get("transcript") or {}).get("lines") or []
        pieces = edit.plan(segments, data.get("script") or [], lines, shots, info.duration, voice_duration, zoom)
        hook_seg = next((s for s in segments if s.get("script_id") == 0), None)
        ctx.log(f"Dựng {len(pieces)} cảnh ngắn (trung bình {voice_duration / max(1, len(pieces)):.1f}s/cảnh) "
                f"từ {len(shots)} cảnh gốc, khớp {len(segments)} đoạn giọng đọc ({voice_duration:.1f}s)"
                + (f"; mở đầu bằng cảnh {pieces[0].src_start:.0f}s của video gốc" if hook_seg and pieces else ""))

        # --- mã hóa từng cảnh (tiến độ lưu ở thư mục tạm → Chạy lại bỏ qua cảnh đã xong)
        key = hashlib.sha1(json.dumps([[x.to_dict() for x in pieces], aspect, fit, blur_subs, bg_volume > 0,
                                       data.get("ocr_events") is not None, data.get("ocr_version"),
                                       vs_render.BLUR_PAD, vs_render.BLUR_BRIDGE], sort_keys=True).encode()).hexdigest()
        state_file = ctx.temp_dir / "edit_state.json"
        state = _load_json(state_file)
        if state.get("key") != key:
            state = {"key": key, "done": []}
        pdir = ctx.temp_dir / "pieces"
        pdir.mkdir(exist_ok=True)
        enc0, enc1 = 0.3, 0.85
        for piece in pieces:
            name = f"piece_{piece.index:04d}.mkv"
            if piece.index in state["done"] and (pdir / name).is_file():
                continue
            ctx.checkpoint()
            frac = piece.out_start / voice_duration
            ctx.report(enc0 + (enc1 - enc0) * frac, f"Đang dựng cảnh {piece.index + 1}/{len(pieces)}")
            ffile = f"piece_{piece.index:04d}.filter"
            (pdir / ffile).write_text(edit.piece_filter(piece, events, v.width, v.height, aspect, fit),
                                      encoding="utf-8")
            part = pdir / (name + ".part.mkv")
            try:
                run_ffmpeg(tools, edit.piece_args(src, piece, ffile, part, bg_volume > 0), ctx, piece.duration,
                           None, cwd=pdir)
                os.replace(part, pdir / name)
            finally:
                part.unlink(missing_ok=True)
            state["done"].append(piece.index)
            _save_json(state_file, state)

        # --- ghép + giọng + chữ
        ctx.report(enc1, "Đang ghép cảnh, giọng đọc và chữ")
        (pdir / "concat.txt").write_text("".join(f"file 'piece_{x.index:04d}.mkv'\n" for x in pieces),
                                         encoding="utf-8")
        caps = edit.caption_events(segments, aspect) if captions else []
        title_end = hook_seg["end"] if hook_seg else min(4.0, voice_duration)
        (pdir / "text.ass").write_text(edit.build_ass(caps, size, aspect, title, title_end if title else 0.0, credit),
                                       encoding="utf-8")
        out = proj / project.VIDEO_MP4
        ensure_not_source(out, src)
        part = proj / (project.VIDEO_MP4 + ".part")
        try:
            run_ffmpeg(tools, edit.final_args("concat.txt", voice_path, "text.ass", part, voice_duration, bg_volume),
                       ctx, voice_duration, ctx.sub_progress(enc1, 0.99), cwd=pdir)
            out_info = verify_video(part, tools, size, voice_duration)
            os.replace(part, out)
        finally:
            part.unlink(missing_ok=True)
        data["edit"] = {"aspect": aspect, "fit": fit, "zoom": zoom, "pieces": len(pieces), "title": title,
                        "credit": credit, "duration": round(out_info.duration, 3)}
        if credit:
            data["credit"] = credit
        project.save(proj, data)
        ctx.report(1.0, "Đã xuất video mới")
        return {"project_dir": str(proj), "video": str(out), "duration": round(out_info.duration, 3),
                "size_bytes": out.stat().st_size, "pieces": len(pieces), "source_shots": len(shots),
                "aspect": aspect, "width": size[0], "height": size[1], "captions": len(caps),
                "blurred": sum(not e.static for e in events)}

    return run


# ================================================================ bước 4: thumbnail + SEO


def validate_seo(params: dict) -> None:
    proj = project.validate_project_dir(params)
    if not (proj / project.SCRIPT_TXT).is_file() and not (proj / project.VOICE_JSON).is_file():
        raise InvalidInput("Dự án chưa có kịch bản.", "Chạy bước 1 trước.")
    for k, limit in (("notes", 500), ("credit", MAX_CREDIT)):
        if not isinstance(params.get(k, ""), str) or len(params.get(k, "")) > limit:
            raise InvalidInput(f"'{k}' tối đa {limit} ký tự.")


def _final_script(proj: Path, data: dict) -> str:
    vmeta = _load_json(proj / project.VOICE_JSON)
    if vmeta.get("segments"):  # lời đọc cuối cùng (người dùng có thể đã sửa kịch bản ở bước 2)
        return "\n\n".join(s["text"] for s in vmeta["segments"])
    return "\n\n".join(x["text"] for x in data.get("script") or [])


def _thumb_boxes(data: dict, t: float, frame_png: Path, engine_loader) -> list[tuple]:
    """Vùng chữ Trung cần che trên khung lấy làm thumbnail (không che logo/watermark đứng yên)."""
    if "ocr_events" in data:
        return [tuple(e["box"]) for e in data["ocr_events"]
                if not e.get("static") and e["start"] - 0.3 <= t <= e["end"] + 0.3]
    import numpy as np
    from PIL import Image

    with Image.open(frame_png) as im:
        geo = vs_ocr.sample_geometry(im.width, im.height, "full")
        small = im.convert("RGB").resize((geo.out_w, geo.out_h))
    bgr = np.ascontiguousarray(np.asarray(small)[:, :, ::-1])
    return [ln.box for ln in vs_ocr.FrameOCR(engine_loader(), geo)(bgr)]


def make_seo_handler(tools_provider: Callable[[], FFmpegTools], settings_provider: Callable[[], Settings]):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        proj = project.validate_project_dir(p)
        data = project.load(proj)
        src = project.source_of(data)
        settings = settings_provider()
        tools = tools_provider()
        credit = p.get("credit", "").strip() or data.get("credit", "")
        font = seo.find_font()
        client = _client(settings)
        info = _probe_source(src, tools, need_audio=False)
        text = _final_script(proj, data)
        if not text.strip():
            raise InvalidInput("Kịch bản rỗng.", "Chạy bước 1 hoặc 2 trước.")

        ctx.report(0.02, f"Đang viết tiêu đề, mô tả, từ khóa ({client.model})")
        res = seo.generate_seo(client, text, p.get("notes", "").strip(), ctx)
        ctx.log(f"Tiêu đề gợi ý: {res.titles[0]}")

        ctx.report(0.35, "Đang chọn khung hình đẹp nhất cho thumbnail")
        scores = seo.score_frames(src, tools, info.duration, ctx, ctx.sub_progress(0.35, 0.8))
        times = seo.pick_times(scores)
        if not times:
            raise ExternalToolError("Không đọc được khung hình nào từ video gốc.", "Kiểm tra file video.")
        engine: list = []

        def _engine():
            if not engine:
                engine.append(vs_ocr.load_engine())
            return engine[0]

        thumbs = []
        for i, t in enumerate(times, 1):
            ctx.checkpoint()
            ctx.report(0.8 + 0.18 * (i - 1) / len(times), f"Đang vẽ thumbnail {i}/{len(times)}")
            frame = seo.grab_frame(src, tools, t, ctx.temp_dir / f"frame{i}.png")
            boxes = _thumb_boxes(data, t, frame, _engine)
            out = proj / project.thumbnail_name(i)
            ensure_not_source(out, src)
            seo.render_thumbnail(frame, res.thumbnail_texts[(i - 1) % len(res.thumbnail_texts)], out, font, boxes)
            thumbs.append({"path": str(out), "time": t, "text": res.thumbnail_texts[(i - 1) % len(res.thumbnail_texts)],
                           "blurred": len(boxes)})
        vcredit = (data.get("voice") or {}).get("credit", "")
        spath = proj / project.SEO_TXT
        vs_render.write_text_atomic(spath, seo.seo_text(res, credit, vcredit))
        data["seo"] = res.to_dict()
        project.save(proj, data)
        ctx.report(1.0, "Đã có thumbnail + SEO")
        return {"project_dir": str(proj), "seo_file": str(spath), "thumbnails": thumbs, **res.to_dict(),
                "note": seo.NO_VOLUME_NOTE}

    return run


def register(registry: TaskRegistry, tools_provider: Callable[[], FFmpegTools],
             settings_provider: Callable[[], Settings], paths: AppPaths) -> None:
    registry.register(TaskSpec(KIND_SCRIPT, "Làm lại video: lấy SUB & viết lại",
                               make_script_handler(tools_provider, settings_provider, paths.models_dir),
                               validate_script))
    registry.register(TaskSpec(KIND_VOICE, "Làm lại video: giọng đọc", make_voice_handler(paths.models_dir, tools_provider),
                               validate_voice))
    registry.register(TaskSpec(KIND_EDIT, "Làm lại video: dựng video", make_edit_handler(tools_provider, settings_provider),
                               validate_edit))
    registry.register(TaskSpec(KIND_SEO, "Làm lại video: thumbnail + SEO",
                               make_seo_handler(tools_provider, settings_provider), validate_seo))
