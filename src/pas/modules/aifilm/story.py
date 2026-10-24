"""Kịch bản phim ngắn: ý tưởng → Ollama viết truyện nhiều nhân vật → văn bản người dùng sửa được → dữ liệu cho các bước sau.

Kịch bản lưu dạng văn bản dễ đọc ('kịch bản.txt'), người dùng sửa tự do rồi các bước sau đọc lại:

    TIÊU ĐỀ: The Forbidden Exam
    PHONG CÁCH: cinematic film still, dark fantasy academy, dramatic light
    NHÂN VẬT: Lyra | nữ | kristin | young woman, long silver hair, blue academy robe

    CẢNH 1 | zoom_in
    NGƯỜI NÓI: Lyra
    ẢNH: close-up of Lyra in a burning library, holding a glowing book
    EN: You shouldn't have opened it.
    VI: Cậu không nên mở nó ra.

Tên nhân vật trong dòng ẢNH được thay bằng ngoại hình của nhân vật đó → mỗi cảnh vẽ cùng một người (gần đúng — mô hình
ảnh không giữ được khuôn mặt tuyệt đối). Giọng: kristin/john/norman hoặc `libritts_r#<số người đọc>`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pas.core.errors import ExternalToolError, InvalidInput
from pas.modules.aifilm.models import EN_VOICES
from pas.modules.vietsub.ocr import has_cjk

if TYPE_CHECKING:
    from pas.jobs.context import JobContext
    from pas.modules.vietsub.translate import OllamaClient

log = logging.getLogger(__name__)

MOTIONS = {
    "zoom_in": "Zoom vào chậm",
    "zoom_out": "Zoom ra chậm",
    "pan_left": "Lia sang trái",
    "pan_right": "Lia sang phải",
    "pan_up": "Lia lên",
    "shake": "Rung (căng thẳng, va chạm)",
    "flash": "Lóe sáng + zoom (phép thuật, bất ngờ)",
}
DEFAULT_MOTION = "zoom_in"
GENDERS = {"nam": "male", "nữ": "female"}
DEFAULT_STYLE = "cinematic film still, fantasy, dramatic lighting, sharp focus, high detail"
MIN_SHOTS, MAX_SHOTS, DEFAULT_SHOTS = 6, 30, 16
MAX_CHARACTERS = 6
MAX_IDEA = 1000
MAX_LINE = 200
MAX_PROMPT_WORDS = 60  # CLIP chỉ đọc 77 token đầu — phần dài hơn bị bỏ qua
NUM_CTX = 8192
ATTEMPTS = 3
# Người đọc của libritts_r (904 người, không có nhãn giới tính): chọn theo cao độ giọng (F0) đo thật trên câu mẫu —
# nam ~95–110 Hz, nữ ~235–265 Hz — để nhân vật nam/nữ nghe đúng giới tính và khác nhau rõ.
LIBRITTS_MALE = (132, 36, 186, 438, 594, 288)
LIBRITTS_FEMALE = (162, 336, 138, 624, 444, 60)
_VOICE_RE = re.compile(r"^(kristin|john|norman|libritts_r#(\d{1,3}))$")


@dataclass
class Character:
    name: str
    gender: str  # "nam" | "nữ"
    voice: str  # "kristin" | "john" | "norman" | "libritts_r#<id>"
    look: str  # ngoại hình (tiếng Anh, dùng cho mô hình ảnh)


@dataclass
class Shot:
    index: int  # 1, 2, …
    motion: str
    speaker: str  # "" = không thoại
    image: str
    en: str
    vi: str


@dataclass
class Story:
    title: str
    style: str
    characters: list[Character] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)

    def character(self, name: str) -> Character | None:
        return next((c for c in self.characters if c.name.lower() == name.lower()), None)


# ---------------------------------------------------------------- giọng


def voice_parts(voice: str) -> tuple[str, int | None]:
    """'libritts_r#132' → ('libritts_r', 132); 'kristin' → ('kristin', None)."""
    key, _, sp = voice.partition("#")
    return key, (int(sp) if sp else None)


def assign_voices(characters: list[Character]) -> None:
    """Giọng khác nhau cho từng nhân vật theo giới tính: john/norman, kristin trước, sau đó người đọc libritts_r."""
    pools = {"nam": ["john", "norman", *(f"libritts_r#{s}" for s in LIBRITTS_MALE)],
             "nữ": ["kristin", *(f"libritts_r#{s}" for s in LIBRITTS_FEMALE)]}
    used = {c.voice for c in characters if c.voice}
    for c in characters:
        if c.voice:
            continue
        pool = pools[c.gender]
        c.voice = next((v for v in pool if v not in used), pool[-1])
        used.add(c.voice)


# ---------------------------------------------------------------- văn bản ⇄ Story


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def format_story(story: Story) -> str:
    out = [f"TIÊU ĐỀ: {_one_line(story.title)}", f"PHONG CÁCH: {_one_line(story.style)}", ""]
    out += [f"NHÂN VẬT: {_one_line(c.name)} | {c.gender} | {c.voice} | {_one_line(c.look)}" for c in story.characters]
    for s in story.shots:
        out += ["", f"CẢNH {s.index} | {s.motion}", f"NGƯỜI NÓI: {_one_line(s.speaker)}", f"ẢNH: {_one_line(s.image)}",
                f"EN: {_one_line(s.en)}", f"VI: {_one_line(s.vi)}"]
    return "\n".join(out) + "\n"


_FIELD = re.compile(r"^(TIÊU ĐỀ|PHONG CÁCH|NHÂN VẬT|CẢNH|NGƯỜI NÓI|ẢNH|EN|VI)\s*:?\s*(.*)$", re.IGNORECASE)


def parse_story(text: str) -> Story:
    """Đọc kịch bản người dùng đã sửa. Sai cú pháp → InvalidInput chỉ rõ dòng nào."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidInput("Chưa có kịch bản.", "Chạy bước 1 'Viết kịch bản' hoặc mở dự án cũ.")
    story = Story("", DEFAULT_STYLE)
    cur: Shot | None = None
    for no, raw in enumerate(text.replace("\r", "").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _FIELD.match(line)
        if not m:
            raise InvalidInput(f"Dòng {no} không hiểu được: {line[:80]!r}",
                               "Mỗi dòng phải bắt đầu bằng TIÊU ĐỀ:, PHONG CÁCH:, NHÂN VẬT:, CẢNH, NGƯỜI NÓI:, ẢNH:, EN: "
                               "hoặc VI:. Dòng bắt đầu bằng # là ghi chú.")
        key, val = m.group(1).upper(), m.group(2).strip()
        if key == "TIÊU ĐỀ":
            story.title = val
        elif key == "PHONG CÁCH":
            story.style = val or DEFAULT_STYLE
        elif key == "NHÂN VẬT":
            story.characters.append(_parse_character(val, no))
        elif key == "CẢNH":
            num, _, motion = (p.strip() for p in val.partition("|"))
            motion = motion or DEFAULT_MOTION
            if motion not in MOTIONS:
                raise InvalidInput(f"Dòng {no}: chuyển động '{motion}' không có.", "Dùng một trong: " + ", ".join(MOTIONS))
            cur = Shot(len(story.shots) + 1, motion, "", "", "", "")
            story.shots.append(cur)
        else:
            if cur is None:
                raise InvalidInput(f"Dòng {no}: '{key}' nằm ngoài cảnh nào.", "Thêm dòng 'CẢNH <số> | zoom_in' phía trên.")
            attr = {"NGƯỜI NÓI": "speaker", "ẢNH": "image", "EN": "en", "VI": "vi"}[key]
            setattr(cur, attr, val)
    _check(story)
    return story


def _parse_character(val: str, no: int) -> Character:
    parts = [p.strip() for p in val.split("|")]
    if len(parts) != 4 or not parts[0]:
        raise InvalidInput(f"Dòng {no}: nhân vật cần 4 phần 'tên | nam/nữ | giọng | ngoại hình'.", f"Đang có: {val[:80]!r}")
    name, gender, voice, look = parts
    if gender.lower() not in GENDERS:
        raise InvalidInput(f"Dòng {no}: giới tính của {name} phải là 'nam' hoặc 'nữ'.")
    m = _VOICE_RE.match(voice)
    if not m or (m.group(2) and int(m.group(2)) >= EN_VOICES["libritts_r"].speakers):
        raise InvalidInput(f"Dòng {no}: giọng '{voice}' của {name} không có.",
                           f"Dùng kristin, john, norman hoặc libritts_r#0 … libritts_r#{EN_VOICES['libritts_r'].speakers - 1}.")
    return Character(name, gender.lower(), voice, look)


def _check(story: Story) -> None:
    if not story.title:
        raise InvalidInput("Kịch bản thiếu dòng 'TIÊU ĐỀ:'.")
    if not story.shots:
        raise InvalidInput("Kịch bản chưa có cảnh nào.", "Mỗi cảnh bắt đầu bằng dòng 'CẢNH <số> | <chuyển động>'.")
    if len(story.shots) > MAX_SHOTS:
        raise InvalidInput(f"Kịch bản có {len(story.shots)} cảnh, tối đa {MAX_SHOTS}.", "Chia thành nhiều phim.")
    if len(story.characters) > MAX_CHARACTERS:
        raise InvalidInput(f"Tối đa {MAX_CHARACTERS} nhân vật.")
    names = [c.name.lower() for c in story.characters]
    if len(set(names)) != len(names):
        raise InvalidInput("Có hai nhân vật trùng tên.")
    for s in story.shots:
        where = f"Cảnh {s.index}"
        if not s.image:
            raise InvalidInput(f"{where} thiếu dòng 'ẢNH:' (mô tả hình bằng tiếng Anh).")
        if s.speaker and story.character(s.speaker) is None:
            raise InvalidInput(f"{where}: người nói '{s.speaker}' không có trong danh sách NHÂN VẬT.",
                               "Sửa tên cho khớp, hoặc để trống 'NGƯỜI NÓI:' nếu cảnh không có thoại.")
        if s.en and not s.speaker:
            raise InvalidInput(f"{where} có lời thoại nhưng chưa có NGƯỜI NÓI.")
        if s.speaker and not s.en:
            raise InvalidInput(f"{where} có NGƯỜI NÓI nhưng thiếu lời thoại 'EN:'.")
        if s.en and not s.vi:
            raise InvalidInput(f"{where} thiếu phụ đề tiếng Việt 'VI:'.")
        for label, val in (("EN", s.en), ("VI", s.vi)):
            if len(val) > MAX_LINE:
                raise InvalidInput(f"{where}: lời {label} quá dài ({len(val)} ký tự, tối đa {MAX_LINE}).",
                                   "Chia thành hai cảnh — mỗi cảnh một câu ngắn.")


# ---------------------------------------------------------------- lời nhắc ảnh


def image_prompt(story: Story, shot: Shot) -> str:
    """Thay tên nhân vật trong mô tả bằng ngoại hình (mô hình ảnh không biết tên riêng), thêm phong cách chung."""
    text = shot.image
    for c in sorted(story.characters, key=lambda c: -len(c.name)):
        pat = re.compile(rf"\b{re.escape(c.name)}\b", re.IGNORECASE)
        first = c.look or ("a man" if c.gender == "nam" else "a woman")
        later = "the man" if c.gender == "nam" else "the woman"
        text = pat.sub(lambda m, n=iter(range(99)): first if next(n) == 0 else later, text)
    prompt = _one_line(f"{text}, {story.style}")
    if len(prompt.split()) > MAX_PROMPT_WORDS:
        log.warning("Cảnh %d: mô tả ảnh dài %d từ — mô hình chỉ đọc khoảng %d từ đầu", shot.index,
                    len(prompt.split()), MAX_PROMPT_WORDS)
    return prompt


def shot_seed(story: Story, shot: Shot, variant: int = 0) -> int:
    """Hạt giống cố định theo nội dung cảnh → chạy lại ra đúng ảnh cũ; 'vẽ lại' tăng variant."""
    h = hashlib.sha1(f"{image_prompt(story, shot)}|{variant}".encode()).digest()
    return int.from_bytes(h[:4], "little") & 0x7FFFFFFF


# ---------------------------------------------------------------- Ollama viết truyện


SYSTEM = (
    "You are a screenwriter for viral vertical short dramas (TikTok/Shorts, 9:16). You write tight, emotional, "
    "visual stories with a strong hook in the first shot, rising conflict, a twist, and a cliffhanger ending. "
    "Dialogue is natural spoken American English. You also write the Vietnamese subtitle for every line: natural, "
    "colloquial Vietnamese (not word-for-word). Never use real people, celebrities or brand names. Answer with JSON only."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "gender": {"type": "string", "enum": ["male", "female"]},
            "look": {"type": "string"}}, "required": ["name", "gender", "look"]}},
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "speaker": {"type": "string"}, "image": {"type": "string"}, "motion": {"type": "string", "enum": list(MOTIONS)},
            "en": {"type": "string"}, "vi": {"type": "string"}}, "required": ["speaker", "image", "motion", "en", "vi"]}},
    },
    "required": ["title", "style", "characters", "shots"],
}


def build_prompt(idea: str, shots: int, feedback: str = "") -> str:
    return (
        f"Story idea from the user (may be in Vietnamese):\n{idea}\n\n"
        f"Write a short drama of exactly {shots} shots (each shot is 2-4 seconds on screen).\n"
        "- title: short catchy English title.\n"
        "- style: one comma-separated visual style phrase for ALL images (e.g. 'cinematic film still, dark fantasy "
        "academy, dramatic lighting, sharp focus'). Photorealistic unless the idea asks otherwise.\n"
        f"- characters: 2-{MAX_CHARACTERS} characters with short distinctive ENGLISH names (plain ASCII, e.g. Lyra, "
        "Kael — even if the idea is in Vietnamese, because the dialogue is read by an English voice); look = fixed appearance as an English "
        "noun phrase (e.g. 'a young woman with long silver hair in a blue academy robe'), max 15 words, no names.\n"
        "- shots: speaker = the character name who speaks (exactly as in characters), or \"\" for a silent shot; "
        "image = what the camera sees, in English, max 25 words, start with the framing (close-up / medium shot / "
        "wide shot), mention characters BY NAME, describe action, emotion and place; motion = camera move that fits "
        "(shake for impact, flash for magic or shock); en = ONE short line the speaker SAYS OUT LOUD (max 15 "
        "words, e.g. 'You think I'm weak?'), never a description of actions (actions go in image), \"\" if silent; "
        "vi = Vietnamese subtitle of en, fully in Vietnamese (no English words), \"\" if silent.\n"
        "- Most shots should have dialogue. Shot 1 is the hook. The last shot is a cliffhanger.\n"
        + (f"\nYour previous answer had problems, fix them: {feedback}\n" if feedback else "")
    )


def _problems(data: dict, shots: int) -> list[str]:
    out = []
    chars = data.get("characters") or []
    names = {str(c.get("name", "")).strip().lower() for c in chars if isinstance(c, dict)}
    if not str(data.get("title", "")).strip():
        out.append("missing title")
    if not 1 <= len(chars) <= MAX_CHARACTERS:
        out.append(f"need 1-{MAX_CHARACTERS} characters")
    if bad := [n for n in names if not n.isascii()]:
        out.append(f"character names {', '.join(bad)} are not English, use plain ASCII English names")
    got = data.get("shots") or []
    if len(got) < max(MIN_SHOTS, int(shots * 0.7)):
        out.append(f"only {len(got)} shots, need {shots}")
    for i, s in enumerate(got, 1):
        if not isinstance(s, dict):
            out.append(f"shot {i} is not an object")
            continue
        sp = str(s.get("speaker", "")).strip().lower()
        if sp and sp not in names and sp not in ("narrator", "none"):
            out.append(f"shot {i}: speaker '{s.get('speaker')}' is not a character")
        if not str(s.get("image", "")).strip():
            out.append(f"shot {i}: empty image")
        if str(s.get("en", "")).strip() and not str(s.get("vi", "")).strip():
            out.append(f"shot {i}: missing Vietnamese subtitle")
        if any(has_cjk(str(s.get(k, ""))) for k in ("en", "vi", "image")):
            out.append(f"shot {i}: contains Chinese characters")
        en, vi = str(s.get("en", "")).strip(), str(s.get("vi", "")).strip()
        if en and _is_stage_direction(en, names):
            out.append(f"shot {i}: en '{en[:40]}' describes an action, write words the speaker says instead")
        if en and vi and (left := _untranslated(en, vi, names)):
            out.append(f"shot {i}: vi still has English words ({left}), translate fully")
    return out


_FIRST_SECOND = re.compile(r"\b(i|i'm|i'll|me|my|mine|we|us|our|you|you're|your|yours)\b")


def _is_stage_direction(en: str, names: set[str]) -> bool:
    """'Prince smirks, ...' / "Silver's eyes glow" — tên nhân vật + động từ, không có ngôi thứ nhất/hai → lời tả cảnh."""
    low = en.lower()
    for n in names:
        if n and (m := re.match(re.escape(n) + r"('s)?\s+[a-z]", low)) and not _FIRST_SECOND.search(low[m.end() - 1:]):
            return True
    return False


def _untranslated(en: str, vi: str, names: set[str]) -> str:
    """Từ tiếng Anh (≥5 chữ, không phải tên) của câu EN còn nằm nguyên trong phụ đề VI."""
    low = vi.lower()
    words = {w for w in re.findall(r"[a-z]{5,}", en.lower()) if w not in names}
    return ", ".join(sorted(w for w in words if w in low))


def to_story(data: dict, shots: int) -> Story:
    chars = []
    for c in data["characters"][:MAX_CHARACTERS]:
        name = _one_line(c.get("name", "")).replace("|", "/")
        if name and all(name.lower() != x.name.lower() for x in chars):
            gender = "nữ" if c.get("gender") == "female" else "nam"
            chars.append(Character(name, gender, "", _one_line(c.get("look", "")).replace("|", "/")))
    assign_voices(chars)
    story = Story(_one_line(data["title"]), _one_line(data.get("style") or DEFAULT_STYLE) or DEFAULT_STYLE, chars)
    for s in data["shots"][:shots]:
        speaker = _one_line(s.get("speaker", ""))
        en, vi = _one_line(s.get("en", ""))[:MAX_LINE], _one_line(s.get("vi", ""))[:MAX_LINE]
        ch = story.character(speaker)
        if ch is None or not en:
            speaker, en, vi = "", "", ""  # "Narrator"/tên lạ → cảnh không thoại (không có giọng kể chuyện)
        motion = s.get("motion") if s.get("motion") in MOTIONS else DEFAULT_MOTION
        story.shots.append(Shot(len(story.shots) + 1, motion, ch.name if ch else "", _one_line(s.get("image", "")),
                                en, vi))
    _check(story)
    return story


def write_story(client: "OllamaClient", idea: str, shots: int, ctx: "JobContext | None" = None) -> Story:
    """Gọi Ollama tối đa ATTEMPTS lần; lần sau kèm danh sách lỗi của lần trước. Hết lượt vẫn lỗi → báo lỗi thật."""
    feedback = ""
    problems: list[str] = []
    for attempt in range(ATTEMPTS):
        if ctx is not None:
            ctx.report(0.05 + 0.9 * attempt / ATTEMPTS, f"Ollama đang viết kịch bản (lần {attempt + 1})")
        data = client.chat_json(build_prompt(idea, shots, feedback), 300 * shots + 600, 0.8, ctx, SYSTEM, SCHEMA,
                                NUM_CTX)
        problems = _problems(data, shots) if data else ["answer was not valid JSON"]
        if not problems:
            try:
                return to_story(data, shots)
            except InvalidInput as exc:
                problems = [exc.message]
        log.warning("Kịch bản lần %d chưa đạt: %s", attempt + 1, "; ".join(problems[:8]))
        feedback = "; ".join(problems[:8])
    raise ExternalToolError(f"Ollama không viết được kịch bản hợp lệ sau {ATTEMPTS} lần ({'; '.join(problems[:3])}).",
                            "Viết ý tưởng rõ hơn (ai, ở đâu, xung đột gì) rồi chạy lại, hoặc giảm số cảnh.")
