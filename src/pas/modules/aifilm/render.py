"""Dựng phim từ ảnh tĩnh: mỗi cảnh = một ảnh + chuyển động máy quay (FFmpeg) dài bằng lời thoại; ghép, lồng tiếng, chữ.

Chuyển động làm bằng zoompan trên ảnh đã phóng 2× (giảm rung do làm tròn tọa độ); "rung" = lệch tâm theo hàm sin
giảm dần; "lóe sáng" = mở đầu từ khung trắng + zoom nhanh. Phụ đề Việt to ở giữa-dưới khung (kiểu phim ngắn TikTok),
dòng tiếng Anh nhỏ bên dưới (tùy chọn), nhãn "Nội dung tạo bằng AI" ở góc trên.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from pas.core.errors import ExternalToolError
from pas.modules.remake.edit import FPS, split_caption
from pas.modules.vietsub import render as vs_render

RESOLUTIONS = {"720p": (720, 1280), "1080p": (1080, 1920)}
DEFAULT_RESOLUTION = "720p"
MIN_SHOT = 2.2  # cảnh không thoại / thoại rất ngắn vẫn đủ để nhìn
LEAD = 0.25  # chờ trước khi nhân vật nói
TRAIL = 0.3  # dừng sau câu (tts đã chèn thêm 0,4 s lặng ở cuối)
TITLE_SECONDS = 2.5
AI_LABEL = "Nội dung tạo bằng AI"
VI_CHARS = {720: 22, 1080: 22}  # chữ to → mỗi dòng ngắn


@dataclass
class Timed:
    index: int
    start: float
    duration: float
    voice_wav: Path | None  # None = cảnh không thoại
    voice_len: float
    vi: str
    en: str

    @property
    def frames(self) -> int:
        return max(1, round(self.duration * FPS))


def timeline(shots: list[tuple[int, Path | None, float, str, str]]) -> list[Timed]:
    """(số cảnh, wav, thời lượng giọng, vi, en) → thời điểm từng cảnh. Thời lượng làm tròn theo khung hình."""
    out, t = [], 0.0
    for index, wav, voice_len, vi, en in shots:
        dur = max(MIN_SHOT, LEAD + voice_len + TRAIL) if wav else MIN_SHOT
        dur = round(dur * FPS) / FPS
        out.append(Timed(index, round(t, 4), dur, wav, voice_len, vi, en))
        t += dur
    return out


def total_duration(items: list[Timed]) -> float:
    return sum(x.frames for x in items) / FPS


# ---------------------------------------------------------------- âm thanh


def build_voice_track(items: list[Timed], out: Path) -> int:
    """Ghép giọng từng câu vào một WAV mono dài đúng bằng phim (đặt tại start + LEAD). Trả về tần số mẫu."""
    rate = None
    clips = []
    for it in items:
        if it.voice_wav is None:
            continue
        with wave.open(str(it.voice_wav), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ExternalToolError(f"Giọng cảnh {it.index} không phải WAV mono 16-bit.", "Bấm Chạy lại.")
            if rate is None:
                rate = w.getframerate()
            elif w.getframerate() != rate:
                raise ExternalToolError(f"Giọng cảnh {it.index} có tần số {w.getframerate()} Hz khác các cảnh khác.",
                                        "Các giọng Piper tiếng Anh đều 22050 Hz — tải lại giọng đọc.")
            clips.append((it, w.readframes(w.getnframes())))
    rate = rate or 22050
    total = round(total_duration(items) * rate)
    buf = bytearray(total * 2)
    for it, pcm in clips:
        pos = round((it.start + LEAD) * rate) * 2
        end = min(len(buf), round((it.start + it.duration) * rate) * 2)  # không tràn sang cảnh sau
        pcm = pcm[: max(0, end - pos)]
        buf[pos:pos + len(pcm)] = pcm
    part = out.with_name(out.name + ".part")
    try:
        with wave.open(str(part), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(buf))
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return rate


# ---------------------------------------------------------------- hình


def motion_filter(motion: str, frames: int, size: tuple[int, int]) -> str:
    """Bộ lọc FFmpeg cho một cảnh: ảnh (1 khung) → `frames` khung có chuyển động, kích thước `size`."""
    w, h = size
    n = max(1, frames - 1)
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_out":
        z, x, y = f"1.18-0.18*on/{n}", center_x, center_y
    elif motion == "pan_left":
        z, x, y = "1.18", f"(iw-iw/zoom)*(1-on/{n})", center_y
    elif motion == "pan_right":
        z, x, y = "1.18", f"(iw-iw/zoom)*on/{n}", center_y
    elif motion == "pan_up":
        z, x, y = "1.18", center_x, f"(ih-ih/zoom)*(1-on/{n})"
    elif motion == "shake":
        amp = f"max(0.25,1-on/{n})"
        z = "1.14"
        x = f"{center_x}+iw*0.018*{amp}*sin(on*2.9)*cos(on*1.3)"
        y = f"{center_y}+ih*0.012*{amp}*sin(on*3.7+1)"
    elif motion == "flash":
        z, x, y = f"1+0.22*pow(on/{n},0.6)", center_x, center_y
    else:  # zoom_in
        z, x, y = f"1+0.18*on/{n}", center_x, center_y
    chain = [
        f"[0:v]scale={2 * w}:{2 * h}:force_original_aspect_ratio=increase:flags=lanczos,crop={2 * w}:{2 * h},setsar=1",
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}",
        # ảnh LCM hơi mờ/nhạt: tăng tương phản + làm nét nhẹ + tối viền kiểu điện ảnh
        "eq=contrast=1.08:saturation=1.12,unsharp=5:5:0.7,vignette=PI/5",
    ]
    if motion == "flash":
        chain.append("fade=t=in:st=0:d=0.35:color=white")
    chain.append("format=yuv420p[v]")
    return ",".join(chain)


def shot_args(image: Path, filter_file: str, frames: int, out: Path) -> list[str]:
    return ["-i", str(image), "-/filter_complex", filter_file, "-map", "[v]", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-an", "-map_metadata", "-1", "-f", "matroska", "-y", str(out)]


# ---------------------------------------------------------------- chữ


def build_ass(items: list[Timed], size: tuple[int, int], title: str, show_en: bool, ai_label: bool) -> str:
    w, h = size
    font = vs_render.FONT
    vi_size = int(w * 0.068)
    en_size = int(w * 0.038)
    side = int(w * 0.07)
    styles = [
        # Phụ đề Việt: chữ trắng đậm viền đen dày + bóng, giữa-dưới khung (chừa chỗ giao diện TikTok)
        f"Style: Vi,{font},{vi_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(3, vi_size // 11)},2,2,{side},{side},{int(h * 0.27)},1",
        f"Style: En,{font},{en_size},&H00E6E6E6,&H00E6E6E6,&H00000000,&H96000000,0,1,0,0,100,100,0,0,1,"
        f"{max(2, en_size // 12)},1,2,{side},{side},{int(h * 0.27)},1",
        f"Style: Title,{font},{int(w * 0.075)},&H0000E5FF,&H0000E5FF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,3,"
        f"{max(4, w // 90)},0,8,{side},{side},{int(h * 0.1)},1",
        f"Style: Label,{font},{max(16, int(w * 0.027))},&H30FFFFFF,&H30FFFFFF,&H80000000,&H00000000,0,0,0,0,"
        f"100,100,0,0,1,1,0,9,{int(w * 0.03)},{int(w * 0.03)},{int(h * 0.025)},1",
    ]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = vs_render._ass_time
    end_all = total_duration(items)
    if ai_label:
        lines.append(f"Dialogue: 3,{t(0)},{t(end_all)},Label,,0,0,0,,{vs_render._ass_text(AI_LABEL)}")
    if title:
        lines.append(f"Dialogue: 2,{t(0)},{t(min(TITLE_SECONDS, end_all))},Title,,0,0,0,,{vs_render._ass_text(title)}")
    chars = VI_CHARS.get(w, 22)
    for it in items:
        if not it.vi:
            continue
        start, end = it.start + LEAD * 0.6, it.start + it.duration - 0.05
        vi = r"\N".join(vs_render._ass_text(p) for p in split_caption(it.vi, chars))
        text = vi
        if show_en and it.en:
            text += r"\N{\rEn}" + r"\N".join(vs_render._ass_text(p) for p in split_caption(it.en, chars * 2))
        lines.append(f"Dialogue: 0,{t(start)},{t(end)},Vi,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def final_args(concat_file: str, voice: Path, ass_file: str, out: Path, duration: float,
               music: Path | None, music_volume: float) -> list[str]:
    inputs = ["-f", "concat", "-safe", "0", "-i", concat_file, "-i", str(voice)]
    graph = f"[0:v]ass=filename={ass_file}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo[vo]"
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        graph += (f";[2:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_volume:.2f},"
                  f"afade=t=out:st={max(0.0, duration - 1.5):.3f}:d=1.5[m];"
                  "[vo][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        graph += ";[vo]anull[a]"
    return [*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-map_metadata", "-1",
            "-f", "mp4", "-y", str(out)]
