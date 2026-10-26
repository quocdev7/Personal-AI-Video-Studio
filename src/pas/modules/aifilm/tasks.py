"""3 tác vụ của "Phim ngắn AI" — mỗi bước đọc/ghi chung thư mục dự án; chạy lại thì dùng lại kết quả đã có.

- aifilm.story:  ý tưởng 1–3 câu → Ollama viết kịch bản nhiều nhân vật (thoại Anh + phụ đề Việt) → 'kịch bản.txt'.
- aifilm.images: kịch bản (người dùng đã sửa) → ảnh từng cảnh (LCM Dreamshaper, CPU). Chỉ vẽ cảnh mới/đã đổi.
- aifilm.render: ảnh + giọng Piper từng câu (mỗi nhân vật một giọng) → phim 9:16 có chuyển động, phụ đề, nhãn AI.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import wave
from pathlib import Path
from typing import Callable

from pas.core.config import Settings
from pas.core.disk import GB, ensure_free_space
from pas.core.errors import InvalidInput
from pas.core.paths import AppPaths
from pas.jobs.context import JobContext
from pas.jobs.registry import TaskRegistry, TaskSpec
from pas.media.ffmpeg import FFmpegTools, probe, run_ffmpeg
from pas.modules.aifilm import imagegen, models, project, render, story
from pas.modules.remake import tts
from pas.modules.remake.tasks import verify_video
from pas.modules.vietsub.translate import OllamaClient

KIND_STORY = "aifilm.story"
KIND_IMAGES = "aifilm.images"
KIND_RENDER = "aifilm.render"
TONE = "expressive"
VOICE_VERSION = 1  # đổi khi cách đọc thay đổi → giọng cũ trong bộ nhớ đệm bị bỏ
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


def _client(settings: Settings) -> OllamaClient:
    client = OllamaClient(settings.ollama_url, settings.translate_model)
    client.ensure_ready()
    return client


def _story_from_params(proj: Path, params: dict) -> story.Story:
    """Kịch bản gửi kèm (người dùng vừa sửa) được kiểm tra rồi lưu; không gửi thì đọc file trong dự án."""
    text = params.get("script")
    if text is None:
        return story.parse_story(project.read_script(proj))
    parsed = story.parse_story(text)
    project.write_text(proj / project.SCRIPT_TXT, story.format_story(parsed))
    return parsed


def _check_output_dir(params: dict) -> None:
    out = params.get("output_dir")
    if out and Path(out).exists() and not Path(out).is_dir():
        raise InvalidInput(f"Thư mục đầu ra không phải thư mục: {out}")


def _create_project(st: story.Story, settings: Settings, output_dir: str | None, idea: str, model: str) -> Path:
    """Thư mục dự án mới '<tiêu đề> - phim AI' + kịch bản + aifilm.json."""
    base = Path(output_dir or settings.default_output_dir or Path.home() / "Videos")
    proj = project.new_project_dir(st.title, base)
    ensure_free_space(proj.parent if proj.parent.exists() else base, int(settings.min_free_disk_gb * GB))
    proj.mkdir(parents=True)
    project.write_text(proj / project.SCRIPT_TXT, story.format_story(st))
    project.save(proj, {"version": project.VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "idea": idea,
                        "model": model, "images": {}, "variants": {}})
    return proj


def _check_script_param(params: dict) -> None:
    text = params.get("script")
    if text is not None:
        if not isinstance(text, str):
            raise InvalidInput("Kịch bản không hợp lệ.")
        story.parse_story(text)


# ================================================================ bước 1: viết kịch bản


def validate_story(params: dict) -> None:
    idea = params.get("idea")
    if not isinstance(idea, str) or len(idea.strip()) < 10:
        raise InvalidInput("Ý tưởng quá ngắn.", "Viết 1–3 câu: nhân vật là ai, ở đâu, xung đột/bí mật gì.")
    if len(idea) > story.MAX_IDEA:
        raise InvalidInput(f"Ý tưởng tối đa {story.MAX_IDEA} ký tự.")
    shots = params.get("shots", story.DEFAULT_SHOTS)
    if not isinstance(shots, int) or isinstance(shots, bool) or not story.MIN_SHOTS <= shots <= story.MAX_SHOTS:
        raise InvalidInput(f"Số cảnh phải từ {story.MIN_SHOTS} đến {story.MAX_SHOTS}.")
    _check_output_dir(params)


def make_story_handler(settings_provider: Callable[[], Settings], client_factory=_client):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        settings = settings_provider()
        idea, shots = p["idea"].strip(), p.get("shots", story.DEFAULT_SHOTS)
        ctx.report(0.01, "Đang kiểm tra Ollama")
        client = client_factory(settings)
        st = story.write_story(client, idea, shots, ctx)
        ctx.checkpoint()
        proj = _create_project(st, settings, p.get("output_dir"), idea, getattr(client, "model", ""))
        text = story.format_story(st)
        talk = sum(1 for s in st.shots if s.en)
        ctx.log(f"Kịch bản '{st.title}': {len(st.characters)} nhân vật, {len(st.shots)} cảnh ({talk} cảnh có thoại)")
        ctx.report(1.0, "Đã viết kịch bản")
        return {"project_dir": str(proj), "script": text, "title": st.title, "shots": len(st.shots),
                "characters": [f"{c.name} ({c.gender}, {c.voice})" for c in st.characters]}

    return run


# ================================================================ bước 2: vẽ ảnh


def image_key(st: story.Story, shot: story.Shot, variant: int) -> tuple[str, int]:
    seed = story.shot_seed(st, shot, variant)
    raw = f"{story.image_prompt(st, shot)}|{seed}|{imagegen.WIDTH}x{imagegen.HEIGHT}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16], seed


def _pool_file(proj: Path, key: str) -> Path:
    return proj / project.POOL / f"{key}.png"


def image_status(proj: Path, st: story.Story, data: dict) -> list[tuple[story.Shot, str, int, bool]]:
    """Mỗi cảnh: (cảnh, mã ảnh, seed, đã có ảnh đúng nội dung chưa)."""
    variants = data.get("variants") or {}
    out = []
    for s in st.shots:
        key, seed = image_key(st, s, int(variants.get(str(s.index), 0)))
        out.append((s, key, seed, _pool_file(proj, key).is_file()))
    return out


def validate_images(params: dict) -> None:
    """Có dự án → vẽ cho dự án đó; chưa có → cần kịch bản dán sẵn, tác vụ tạo dự án mới (bỏ qua bước 1)."""
    if params.get("project_dir") or not (isinstance(params.get("script"), str) and params["script"].strip()):
        project.validate_project_dir(params)
    _check_script_param(params)
    _check_output_dir(params)
    regen = params.get("regenerate", [])
    if not isinstance(regen, list) or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in regen):
        raise InvalidInput("Danh sách cảnh cần vẽ lại không hợp lệ.", "Ví dụ: 2, 5, 7")
    if not isinstance(params.get("fast", False), bool):
        raise InvalidInput("Tùy chọn 'vẽ nhanh' không hợp lệ.")


def make_images_handler(models_dir: Path, settings_provider: Callable[[], Settings],
                        generator_factory: Callable[[Path], imagegen.Generator] = imagegen.load):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        if p.get("project_dir"):
            proj = project.validate_project_dir(p)
            st = _story_from_params(proj, p)
        else:
            st = story.parse_story(p["script"])
            proj = _create_project(st, settings_provider(), p.get("output_dir"), "", "")
            ctx.log(f"Tạo dự án mới từ kịch bản dán sẵn: {proj}")
        data = project.load(proj)
        steps = imagegen.FAST_STEPS if p.get("fast") else imagegen.STEPS
        variants = data.setdefault("variants", {})
        for i in p.get("regenerate", []):
            if i > len(st.shots):
                raise InvalidInput(f"Không có cảnh {i} (kịch bản có {len(st.shots)} cảnh).")
            variants[str(i)] = int(variants.get(str(i), 0)) + 1
        project.save(proj, data)

        status = image_status(proj, st, data)
        todo = [x for x in status if not x[3]]
        (proj / project.POOL).mkdir(parents=True, exist_ok=True)
        generated = 0
        if todo:
            ensure_free_space(proj, len(todo) * 2_000_000 + int(settings_provider().min_free_disk_gb * GB))
            ctx.report(0.02, "Đang nạp mô hình vẽ ảnh (~10 giây)")
            gen = generator_factory(models.require_lcm(models_dir))
            t0 = time.monotonic()
            for n, (shot, key, seed, _) in enumerate(todo):
                ctx.checkpoint()
                eta = ""
                if n:
                    left = (time.monotonic() - t0) / n * (len(todo) - n)
                    eta = f", còn khoảng {left / 60:.0f} phút" if left >= 90 else f", còn khoảng {left:.0f} giây"
                ctx.report(0.05 + 0.9 * n / len(todo), f"Đang vẽ cảnh {shot.index} ({n + 1}/{len(todo)}{eta})")
                prompt = story.image_prompt(st, shot)
                ctx.log(f"Cảnh {shot.index}: {prompt}")
                img = gen.generate(prompt, seed, steps, lambda: ctx.is_cancelled)
                out = _pool_file(proj, key)
                part = out.with_name(out.name + ".part")
                try:
                    img.save(part, format="PNG")
                    os.replace(part, out)
                finally:
                    part.unlink(missing_ok=True)
                generated += 1
        # bản sao dễ xem theo số cảnh; xóa bản thừa khi kịch bản bớt cảnh
        images_dir = proj / project.IMAGES
        for old in images_dir.glob("cảnh *.png"):
            old.unlink()
        files = []
        for shot, key, _, _ in status:
            dst = proj / project.shot_image_name(shot.index)
            shutil.copyfile(_pool_file(proj, key), dst)
            files.append(str(dst))
        data["images"] = {str(s.index): key for s, key, _, _ in status}
        project.save(proj, data)
        ctx.report(1.0, "Đã vẽ xong ảnh")
        return {"project_dir": str(proj), "images": files, "generated": generated, "reused": len(status) - generated,
                "steps": steps}

    return run


# ================================================================ bước 3: dựng phim


def validate_render(params: dict) -> None:
    project.validate_project_dir(params)
    _check_script_param(params)
    if params.get("resolution", render.DEFAULT_RESOLUTION) not in render.RESOLUTIONS:
        raise InvalidInput(f"Độ phân giải không hợp lệ: {params.get('resolution')!r}")
    for k in ("show_en", "ai_label", "show_title"):
        if not isinstance(params.get(k, True), bool):
            raise InvalidInput(f"Tùy chọn {k} không hợp lệ.")
    music = params.get("music")
    if music:
        path = Path(music)
        if not path.is_file():
            raise InvalidInput(f"Không tìm thấy file nhạc nền: {path}")
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise InvalidInput(f"'{path.suffix}' không phải file âm thanh được hỗ trợ.",
                               "Hỗ trợ: " + ", ".join(sorted(e.lstrip('.').upper() for e in AUDIO_EXTENSIONS)))
    vol = params.get("music_volume", 0.15)
    if not isinstance(vol, (int, float)) or isinstance(vol, bool) or not 0 < vol <= 1:
        raise InvalidInput("Âm lượng nhạc nền phải từ 1 đến 100%.")


def credits_text(st: story.Story, voices: set[str]) -> str:
    lines = [f"Phim: {st.title}", "", "Nội dung (kịch bản, hình ảnh, giọng đọc) được tạo bằng AI trên máy cục bộ.", "",
             f"Hình ảnh: mô hình LCM Dreamshaper v7 — {models.LCM.license}.", "Giọng đọc:"]
    for key in sorted(voices):
        v = models.EN_VOICES[key]
        lines.append(f"- Piper en_US-{key}: {v.pack.license}. {v.pack.note}")
    lines += ["", "Lưu ý: ảnh AI có thể vô tình giống tác phẩm có sẵn — xem lại từng cảnh trước khi đăng.",
              "Nhạc nền (nếu có) do bạn chọn — bạn tự chịu trách nhiệm về bản quyền của file nhạc đó."]
    return "\n".join(lines) + "\n"


def make_render_handler(models_dir: Path, tools_provider: Callable[[], FFmpegTools],
                        settings_provider: Callable[[], Settings]):
    def run(ctx: JobContext) -> dict:
        p = ctx.params
        proj = project.validate_project_dir(p)
        data = project.load(proj)
        st = _story_from_params(proj, p)
        tools = tools_provider()
        settings = settings_provider()
        size = render.RESOLUTIONS[p.get("resolution", render.DEFAULT_RESOLUTION)]
        music = Path(p["music"]) if p.get("music") else None

        status = image_status(proj, st, data)
        missing = [s.index for s, _, _, ok in status if not ok]
        if missing:
            raise InvalidInput(f"Chưa có ảnh cho cảnh {', '.join(map(str, missing))} (hoặc mô tả ảnh đã đổi).",
                               "Bấm 'Vẽ ảnh' ở bước 2 trước khi dựng phim.")

        # --- giọng từng câu (bộ nhớ đệm theo nội dung: sửa một câu chỉ đọc lại câu đó)
        voice_dir = proj / project.VOICES
        voice_dir.mkdir(exist_ok=True)
        lines = [s for s in st.shots if s.en]
        loaded: dict[str, object] = {}
        used: set[str] = set()
        clips: dict[int, tuple[Path, float]] = {}
        tone = tts.TONES[TONE]
        for n, s in enumerate(lines):
            ctx.checkpoint()
            ctx.report(0.02 + 0.3 * n / max(1, len(lines)), f"Đang đọc lời thoại {n + 1}/{len(lines)}")
            ch = st.character(s.speaker)
            key, speaker = story.voice_parts(ch.voice)
            used.add(key)
            text = tts.clean_text(s.en)
            h = hashlib.sha1(json.dumps([VOICE_VERSION, key, speaker, text, TONE]).encode()).hexdigest()[:16]
            wav = voice_dir / f"{h}.wav"
            if not wav.is_file():
                if key not in loaded:
                    loaded[key] = tts.load_voice(models.require_en_voice(models_dir, key))
                tts.synthesize(loaded[key], text, tone, speaker, wav, ctx)
            with wave.open(str(wav), "rb") as w:
                clips[s.index] = (wav, w.getnframes() / w.getframerate())
        items = render.timeline([(s.index, *(clips.get(s.index) or (None, 0.0)), s.vi, s.en) for s in st.shots])
        duration = render.total_duration(items)
        ensure_free_space(proj, int(duration * 3e6) + int(settings.min_free_disk_gb * GB))

        # --- từng cảnh: ảnh + chuyển động
        work = ctx.temp_dir / "film"
        work.mkdir(exist_ok=True)
        render.build_voice_track(items, work / "voice.wav")
        enc0, enc1 = 0.33, 0.8
        by_index = {s.index: (s, key) for s, key, _, _ in status}
        for it in items:
            ctx.checkpoint()
            ctx.report(enc0 + (enc1 - enc0) * it.start / duration, f"Đang dựng cảnh {it.index}/{len(items)}")
            shot, key = by_index[it.index]
            ffile = f"shot_{it.index:03d}.filter"
            (work / ffile).write_text(render.motion_filter(shot.motion, it.frames, size), encoding="utf-8")
            out = work / f"shot_{it.index:03d}.mkv"
            part = work / (out.name + ".part.mkv")
            try:
                run_ffmpeg(tools, render.shot_args(_pool_file(proj, key), ffile, it.frames, part), ctx,
                           it.duration, None, cwd=work)
                os.replace(part, out)
            finally:
                part.unlink(missing_ok=True)

        # --- ghép + giọng + chữ (+ nhạc nền)
        ctx.report(enc1, "Đang ghép cảnh, giọng đọc và phụ đề")
        (work / "concat.txt").write_text("".join(f"file 'shot_{it.index:03d}.mkv'\n" for it in items), encoding="utf-8")
        title = st.title if p.get("show_title", True) else ""
        (work / "text.ass").write_text(render.build_ass(items, size, title, p.get("show_en", False),
                                                        p.get("ai_label", True)), encoding="utf-8")
        if music is not None:
            probe(music, tools)  # file hỏng → báo lỗi rõ trước khi dựng
        out = proj / project.VIDEO_MP4
        part = proj / (project.VIDEO_MP4 + ".part")
        try:
            run_ffmpeg(tools, render.final_args("concat.txt", work / "voice.wav", "text.ass", part, duration, music,
                                                float(p.get("music_volume", 0.15))),
                       ctx, duration, ctx.sub_progress(enc1, 0.99), cwd=work)
            info = verify_video(part, tools, size, duration)
            os.replace(part, out)
        finally:
            part.unlink(missing_ok=True)
        project.write_text(proj / project.CREDITS_TXT, credits_text(st, used))
        data["render"] = {"size": list(size), "duration": round(info.duration, 3), "shots": len(items),
                          "voices": sorted(used), "music": str(music) if music else ""}
        project.save(proj, data)
        ctx.report(1.0, "Đã xuất phim")
        return {"project_dir": str(proj), "video": str(out), "duration": round(info.duration, 3),
                "size_bytes": out.stat().st_size, "width": size[0], "height": size[1], "shots": len(items),
                "lines": len(lines), "credits": str(proj / project.CREDITS_TXT)}

    return run


def register(registry: TaskRegistry, tools_provider: Callable[[], FFmpegTools],
             settings_provider: Callable[[], Settings], paths: AppPaths) -> None:
    registry.register(TaskSpec(KIND_STORY, "Phim ngắn AI: viết kịch bản", make_story_handler(settings_provider),
                               validate_story))
    registry.register(TaskSpec(KIND_IMAGES, "Phim ngắn AI: vẽ ảnh",
                               make_images_handler(paths.models_dir, settings_provider), validate_images))
    registry.register(TaskSpec(KIND_RENDER, "Phim ngắn AI: dựng phim",
                               make_render_handler(paths.models_dir, tools_provider, settings_provider),
                               validate_render))
