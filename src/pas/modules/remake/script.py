"""Dịch đúng ngữ cảnh + viết lại lời kể bằng LLM cục bộ (Ollama), có kiểm tra đầu ra.

1. Ngữ cảnh: lĩnh vực + tóm tắt + nghĩa (tiếng Anh) của thuật ngữ/tên riêng → đưa vào mọi lượt dịch để dịch đúng nghĩa.
   Hỏi bằng tiếng Anh vì qwen2.5:7b đọc transcript tiếng Trung thì trả lời luôn bằng tiếng Trung, và tự chọn thuật ngữ
   tiếng Việt chuyên ngành rất kém ("pavilion" → "cánh đồng") nhưng hiểu nghĩa tiếng Anh đúng (đã thử trên video thật).
2. Dịch theo lô, kèm vài câu trước đó (đã dịch) làm ngữ cảnh; câu hỏng được dịch lại riêng.
3. Viết lại: từng phần thân bài thành đoạn lời kể tự nhiên (mỗi đoạn ghi rõ dòng nguồn để dựng hình khớp lời),
   rồi một câu mở đầu gây tò mò (hook — lấy từ chi tiết hấp dẫn nhất, kể cả ở cuối video) và lời kêu gọi cuối.
   Không cho thêm sự kiện/số liệu không có trong bản gốc. Đầu ra hỏng → thử lại 1 lần → vẫn hỏng thì báo lỗi.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pas.core.errors import ExternalToolError
from pas.modules.remake.asr import LANGUAGES, Line
from pas.modules.vietsub.ocr import has_cjk
from pas.modules.vietsub.translate import SCHEMA as TRANSLATE_SCHEMA
from pas.modules.vietsub.translate import OllamaClient, parse_items

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

NUM_CTX = 8192
BATCH_SIZE = 8
CONTEXT_LINES = 3
BODY_CHUNK = 30  # số dòng transcript mỗi lượt viết lại
MIN_COVERAGE = 0.6  # tỉ lệ dòng nguồn phải được dùng trong một phần viết lại
MAX_FAILED_RATIO = 0.2
CONTEXT_CHARS = 6000
STYLES = {
    "story": "Kể chuyện cuốn hút, như người dẫn kênh YouTube giải thích cho bạn bè",
    "short": "Nhanh, gọn, nhiều nhịp — hợp video ngắn TikTok/Shorts",
    "doc": "Điềm tĩnh, rõ ràng — kiểu phim tài liệu",
}


@dataclass
class ScriptPart:
    id: int
    role: str  # hook | body | cta
    text: str
    src: tuple[int, int] | None  # (dòng đầu, dòng cuối) của transcript; None = không gắn với cảnh cụ thể

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role, "text": self.text, "src": list(self.src) if self.src else None}

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptPart":
        src = d.get("src")
        return cls(int(d["id"]), str(d["role"]), str(d["text"]), (int(src[0]), int(src[1])) if src else None)


LANGUAGE_EN = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese"}


def lang_name(code: str) -> str:
    return LANGUAGES.get(code, code)


def _clean(text) -> str:
    return " ".join(str(text).split()) if isinstance(text, str) else ""


# ---------------------------------------------------------------- ngữ cảnh

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "summary": {"type": "string"},
        "glossary": {"type": "array", "items": {
            "type": "object", "properties": {"src": {"type": "string"}, "meaning": {"type": "string"}},
            "required": ["src", "meaning"]}},
    },
    "required": ["field", "summary", "glossary"],
}
CONTEXT_SYSTEM = ("You are a professional subtitle editor. Return JSON only. Every value except glossary[].src must be "
                  "written in English and must not contain any Chinese, Japanese or Korean characters.")
MAX_GLOSSARY = 12


def transcript_text(lines: list[Line], field: str = "text", limit: int = CONTEXT_CHARS) -> str:
    out, n = [], 0
    for ln in lines:
        t = getattr(ln, field) or ln.text
        if n + len(t) > limit:
            out.append("…")
            break
        out.append(t)
        n += len(t) + 1
    return "\n".join(out)


def context_prompt(text: str, lang: str) -> str:
    return (f"TRANSCRIPT ({LANGUAGE_EN.get(lang, lang)}, speech recognition output, may contain homophone errors):\n"
            f"{text}\n\nTASK:\n"
            "- field: the domain of this video in a few English words (e.g. gemstone cutting, home cooking).\n"
            "- summary: 2-3 English sentences: what the video is about and who does what.\n"
            f"- glossary: up to {MAX_GLOSSARY} most important domain terms and proper names. src = exact text copied "
            "from the transcript; meaning = what it means in this domain, in English.\n"
            "Do not invent facts that are not in the transcript.")


def parse_context(data, source_text: str) -> dict | None:
    """{'topic': str, 'glossary': [{'src','en'}]} hoặc None nếu thiếu tóm tắt / còn chữ CJK.

    Thực tế mô hình thường ghi `src` bằng tiếng Anh dù được yêu cầu chép nguyên văn → chấp nhận thuật ngữ chữ Latin;
    `src` chữ Hán thì phải có trong transcript (không thì là bịa). Bỏ mục có nghĩa còn chữ CJK."""
    if not isinstance(data, dict):
        return None
    field, summary = _clean(data.get("field")), _clean(data.get("summary"))
    if not summary or has_cjk(summary) or has_cjk(field) or len(summary) > 1000:
        return None
    glossary, seen = [], set()
    for g in data.get("glossary") or []:
        if not isinstance(g, dict):
            continue
        src, meaning = _clean(g.get("src")), _clean(g.get("meaning"))
        if (src and meaning and (src in source_text or not has_cjk(src)) and src not in seen and not has_cjk(meaning)
                and len(src) <= 40 and len(meaning) <= 120):
            seen.add(src)
            glossary.append({"src": src, "en": meaning})
    topic = f"{field[:60]}. {summary}" if field else summary
    return {"topic": topic, "glossary": glossary[:MAX_GLOSSARY]}


def build_context(client: OllamaClient, lines: list[Line], lang: str, ctx: "JobContext | None" = None) -> dict:
    """{'topic': str, 'glossary': [{'src','en'}]}. Chỉ là thông tin phụ trợ cho bước dịch; hỏng thì dịch không có nó."""
    text = transcript_text(lines)
    for temp in (0.2, 0.5):
        got = parse_context(client.chat_json(context_prompt(text, lang), 900, temp, ctx, CONTEXT_SYSTEM,
                                             CONTEXT_SCHEMA, NUM_CTX), text)
        if got:
            return got
    log.warning("LLM không tóm tắt được chủ đề video")
    return {"topic": "", "glossary": []}


# ---------------------------------------------------------------- dịch


EN_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "en": {"type": "string"}},
        "required": ["id", "en"]}}},
    "required": ["items"],
}


def _terms(glossary: list[dict]) -> str:
    # 'en' = nghĩa tiếng Anh (bản hiện tại); 'vi' = bản dịch cố định (dự án tạo bởi bản cũ).
    return "; ".join(f"{g['src']} ({g['en']})" if g.get("en") else f"{g['src']} = {g.get('vi', '')}"
                     for g in glossary)


def english_system(lang: str, topic: str, glossary: list[dict]) -> str:
    gl = _terms(glossary)
    return (f"You translate spoken lines of a {LANGUAGE_EN.get(lang, lang)} video into natural English. "
            + (f"The video: {topic} " if topic else "")
            + (f"Key terms: {gl}. " if gl else "")
            + "The source is speech-recognition output and may contain homophone errors; translate the intended "
              "meaning. Lines under 'Previous lines' are context only — do not translate them. Write only English. "
              'Return JSON {"items":[{"id":number,"en":"English"}]} with every requested id.')


def translate_system(lang: str, topic: str, glossary: list[dict]) -> str:
    """Bước tiếng Anh → tiếng Việt (`lang` chỉ để ghi chú nguồn gốc)."""
    return ("You translate subtitle lines of a video from English into natural spoken Vietnamese. "
            + (f"The video: {topic} " if topic else "")
            # Không nêu ví dụ tên vật bị dịch sai trong prompt: thử thật, câu "a sapphire is not kim cương" lại làm
            # mô hình viết "kim cương" nhiều hơn; quy tắc "giữ nguyên tên tiếng Anh" thì đúng ở mọi lô thử.
            + "Use the usual Vietnamese terms of this field. Keep the names of specific materials, species, products, "
              "places and people exactly as written in English (sapphire stays sapphire) — never replace them with "
              "a different thing. Keep numbers and units. Lines under 'Previous lines' are context only — do not "
              "translate them. Write only Vietnamese. "
              'Return JSON {"items":[{"id":number,"vi":"Vietnamese"}]} with every requested id.')


def _translate_prompt(lines: list[Line], batch: list[Line], src: str = "text", dst: str = "en") -> str:
    first = batch[0].id
    prev = [ln for ln in lines if first - CONTEXT_LINES <= ln.id < first]
    head = ""
    if prev:
        head = "Previous lines:\n" + "\n".join(
            f"- {getattr(ln, src)}" + (f" → {getattr(ln, dst)}" if getattr(ln, dst) else "") for ln in prev) + "\n\n"
    return head + "Translate:\n" + "\n".join(f"{ln.id}: {getattr(ln, src)}" for ln in batch)


def _budget(batch: list[Line], src: str = "text") -> int:
    # Chữ Hán ~1 token/chữ → 4 token đầu ra/chữ; chữ Latin ~4 ký tự/token → 1 token đầu ra/ký tự là dư.
    return 96 + sum(24 + (4 if src == "text" else 1) * len(getattr(ln, src)) for ln in batch)


def _english_items(data, wanted: set[int]) -> dict[int, str]:
    return parse_items({"items": [{"id": x.get("id"), "vi": x.get("en")} for x in data.get("items") or []
                                  if isinstance(x, dict)]} if isinstance(data, dict) else {}, wanted)


def _pass(client: OllamaClient, lines: list[Line], todo: list[Line], src: str, dst: str, system: str, ctx,
          on_batch, on_progress, schema: dict, parse) -> list[int]:
    """Một lượt dịch theo lô `src` → `dst`; câu thiếu/hỏng được dịch lại riêng một lần. Trả về id câu hỏng."""
    failed: list[int] = []
    total = max(1, len(todo))
    for b in range(0, len(todo), BATCH_SIZE):
        if ctx is not None:
            ctx.checkpoint()
        batch = todo[b:b + BATCH_SIZE]
        got = parse(client.chat_json(_translate_prompt(lines, batch, src, dst), _budget(batch, src), 0.2, ctx,
                                     system, schema, NUM_CTX), {ln.id for ln in batch})
        for ln in batch:
            if ln.id not in got:
                one = parse(client.chat_json(_translate_prompt(lines, [ln], src, dst), _budget([ln], src), 0.5, ctx,
                                             system, schema, NUM_CTX), {ln.id})
                if ln.id in one:
                    got[ln.id] = one[ln.id]
                else:
                    failed.append(ln.id)
                    log.warning("Không dịch được câu %s (%s → %s): %r", ln.id, src, dst, getattr(ln, src))
            setattr(ln, dst, got.get(ln.id, ""))
        if on_batch:
            on_batch()
        if on_progress:
            on_progress(min(1.0, (b + len(batch)) / total))
    return failed


def translate(client: OllamaClient, lines: list[Line], lang: str, context: dict, ctx: "JobContext | None" = None,
              on_batch: Callable[[], None] | None = None,
              on_progress: Callable[[float], None] | None = None) -> list[int]:
    """Điền `Line.vi` (bỏ qua câu đã có từ lần chạy trước). Trả về id các câu không dịch được.

    Dịch qua tiếng Anh: thử trên video thật (tiếng Trung), qwen2.5:7b dịch thẳng sang tiếng Việt hay chèn chữ Hán
    vào giữa câu ("Nhưng hiệu果截然不同") và đổi tên vật ("粉蓝宝" → "kim cương"), còn bản tiếng Anh đúng gần như
    mọi câu và bước Anh → Việt không còn lẫn chữ Hán. Đổi lại tốn gấp đôi số lượt gọi mô hình."""
    if lang == "vi":
        for ln in lines:
            ln.vi = ln.vi or ln.text
        return []
    topic, glossary = context.get("topic", ""), context.get("glossary", [])
    todo = [ln for ln in lines if not ln.vi]
    if lang == "en":
        for ln in todo:
            ln.en = ln.en or ln.text
        failed_en: list[int] = []
    else:
        need = [ln for ln in todo if not ln.en]
        failed_en = _pass(client, lines, need, "text", "en", english_system(lang, topic, glossary), ctx, on_batch,
                          _sub(on_progress, 0.0, 0.5), EN_SCHEMA, _english_items)
    ready = [ln for ln in todo if ln.en]
    failed_vi = _pass(client, lines, ready, "en", "vi", translate_system(lang, topic, glossary), ctx, on_batch,
                      _sub(on_progress, 0.5 if lang != "en" else 0.0, 1.0), TRANSLATE_SCHEMA, parse_items)
    return sorted(failed_en + failed_vi)


def _sub(cb: Callable[[float], None] | None, a: float, b: float) -> Callable[[float], None] | None:
    return (lambda f: cb(a + (b - a) * f)) if cb else None


# ---------------------------------------------------------------- viết lại

BODY_SCHEMA = {
    "type": "object",
    "properties": {"parts": {"type": "array", "items": {
        "type": "object",
        "properties": {"from": {"type": "integer"}, "to": {"type": "integer"}, "text": {"type": "string"}},
        "required": ["from", "to", "text"]}}},
    "required": ["parts"],
}
HOOK_SCHEMA = {
    "type": "object",
    "properties": {"hook": {"type": "string"}, "from": {"type": "integer"}, "to": {"type": "integer"},
                   "cta": {"type": "string"}},
    "required": ["hook", "from", "to", "cta"],
}

_RULES = ("QUY TẮC: chỉ dùng thông tin có trong bản dịch — không thêm sự kiện, số liệu, tên, giá tiền hay lời hứa "
          "nào khác; viết tiếng Việt văn nói tự nhiên, câu ngắn, dễ đọc thành tiếng; không dùng emoji, không ký hiệu "
          "đặc biệt, viết số bằng chữ số; gọi vật, chất liệu, người đúng tên như trong bản dịch, không đổi sang tên "
          "khác.")


def body_system(topic: str, style: str) -> str:
    return (
        "Bạn viết lại lời kể cho video. " + (f"Video nói về: {topic} " if topic else "")
        + f"Văn phong: {STYLES.get(style, STYLES['story'])}. "
        "Đầu vào là các dòng bản dịch có số thứ tự. Gộp các dòng liền nhau thành từng đoạn lời kể 1–3 câu, mạch lạc, "
        "giữ người xem tò mò đến đoạn sau (gợi mở, đặt câu hỏi, nhấn vào điểm bất ngờ). Ngắn hơn bản dịch khoảng "
        "20–30%, bỏ câu thừa/lặp. Mỗi đoạn ghi 'from'/'to' là số dòng nguồn đầu và cuối, theo đúng thứ tự, không "
        "chồng lấn. " + _RULES + ' Trả về JSON {"parts":[{"from":số,"to":số,"text":"đoạn lời kể"}]}.'
    )


def hook_system(topic: str, style: str) -> str:
    return (
        "Bạn là biên kịch video ngắn. " + (f"Video nói về: {topic} " if topic else "")
        + f"Văn phong: {STYLES.get(style, STYLES['story'])}. "
        "Từ bản dịch có đánh số, viết: \"hook\": 1–2 câu mở đầu (dưới 30 từ) gây tò mò mạnh để người xem không lướt "
        "qua — dựa vào chi tiết bất ngờ/hấp dẫn nhất trong video (có thể ở cuối video), nhưng không tiết lộ hết kết "
        "quả; tự viết câu mới, không chép nguyên văn bản dịch; không dùng lời chào hay giới thiệu kênh; \"from\"/\"to\": số dòng nguồn chứa chi tiết đó (để chiếu cảnh tương ứng lúc mở đầu); \"cta\": 1 câu "
        "kết ngắn mời người xem bình luận/theo dõi, gắn với nội dung video. " + _RULES
    )


def _strip_ids(text: str, ids: set[int]) -> str:
    """Bỏ số thứ tự dòng mô hình chép theo từ đầu vào ("10: Từ góc này… 11: Khi nhìn…") — chỉ số là id dòng."""
    return " ".join(re.sub(r"(?:^|(?<=\s))(\d+)\s*:\s+", lambda m: "" if int(m.group(1)) in ids else m.group(0),
                           text).split())


def _numbered(lines: list[Line]) -> str:
    return "\n".join(f"{ln.id}: {ln.vi or ln.text}" for ln in lines)


def _valid_text(text: str) -> bool:
    return bool(text) and not has_cjk(text) and len(text) <= 1200


def parse_body(data: dict, chunk: list[Line]) -> list[tuple[int, int, str]]:
    """Đoạn hợp lệ: from ≤ to trong phạm vi phần, chữ Việt không rỗng; sắp theo thứ tự, bỏ đoạn chồng lấn."""
    lo, hi = chunk[0].id, chunk[-1].id
    parts = []
    for p in (data.get("parts") if isinstance(data, dict) else None) or []:
        if not isinstance(p, dict):
            continue
        a, b, text = p.get("from"), p.get("to"), _strip_ids(_clean(p.get("text")), {ln.id for ln in chunk})
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
            continue
        a, b = max(lo, min(a, b)), min(hi, max(a, b))
        if a <= b and _valid_text(text):
            parts.append((a, b, text))
    parts.sort()
    out: list[tuple[int, int, str]] = []
    for a, b, text in parts:
        if out and a <= out[-1][1]:
            if b <= out[-1][1]:
                continue  # nằm gọn trong đoạn trước: trùng lặp
            a = out[-1][1] + 1
        out.append((a, b, text))
    return out


def coverage(parts: list[tuple[int, int, str]], chunk: list[Line]) -> float:
    covered = sum(b - a + 1 for a, b, _ in parts)
    return covered / max(1, len(chunk))


def rewrite_body(client: OllamaClient, chunk: list[Line], topic: str, style: str,
                 ctx: "JobContext | None" = None) -> list[tuple[int, int, str]]:
    budget = 200 + sum(3 * len(ln.vi or ln.text) for ln in chunk)
    best: list[tuple[int, int, str]] = []
    for temp in (0.4, 0.7):
        parts = parse_body(client.chat_json(_numbered(chunk), budget, temp, ctx, body_system(topic, style),
                                            BODY_SCHEMA, NUM_CTX), chunk)
        if coverage(parts, chunk) >= MIN_COVERAGE:
            return parts
        if coverage(parts, chunk) > coverage(best, chunk):
            best = parts
    raise ExternalToolError(
        f"Mô hình {client.model} không viết lại được đoạn dòng {chunk[0].id}–{chunk[-1].id} (chỉ dùng "
        f"{coverage(best, chunk):.0%} nội dung sau 2 lần thử).",
        "Bấm 'Chạy lại' (các đoạn đã xong được giữ). Nếu lặp lại, chọn mô hình lớn hơn trong Cài đặt.")


def write_hook(client: OllamaClient, lines: list[Line], topic: str, style: str,
               ctx: "JobContext | None" = None) -> tuple[str, tuple[int, int], str]:
    # Bỏ phần chào/giới thiệu kênh ở đầu: thử thật cho thấy mô hình hay lấy luôn lời chào làm hook → hook lặp lại
    # nguyên đoạn mở đầu của thân bài.
    skip = intro_lines(len(lines))
    text = _numbered(lines[skip:])
    if len(text) > CONTEXT_CHARS * 2:
        text = text[:CONTEXT_CHARS * 2] + "\n…"
    ids = {ln.id for ln in lines[skip:]}
    for temp in (0.6, 0.9):
        data = client.chat_json(text, 400, temp, ctx, hook_system(topic, style), HOOK_SCHEMA, NUM_CTX)
        if not isinstance(data, dict):
            continue
        hook, cta = _strip_ids(_clean(data.get("hook")), ids), _strip_ids(_clean(data.get("cta")), ids)
        a, b = data.get("from"), data.get("to")
        if not (_valid_text(hook) and _valid_text(cta) and len(hook) <= 300 and len(cta) <= 300):
            continue
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)) or a not in ids or b not in ids:
            a = b = None
        elif _is_copy(hook, [ln for ln in lines if min(a, b) <= ln.id <= max(a, b)]):
            # Thử thật: mô hình chép nguyên văn các dòng nguồn làm "hook" — không gây tò mò gì, viết lại.
            log.info("Hook chép nguyên văn dòng %s–%s, thử lại", a, b)
            continue
        return hook, (min(a, b), max(a, b)) if a is not None else None, cta
    raise ExternalToolError(f"Mô hình {client.model} không viết được câu mở đầu (hook) hợp lệ sau 2 lần thử.",
                            "Bấm 'Chạy lại'. Nếu lặp lại, chọn mô hình lớn hơn trong Cài đặt.")


def _is_copy(hook: str, src: list[Line]) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", " ", s.lower()).strip()
    return bool(src) and norm(hook) == norm(" ".join(ln.vi or ln.text for ln in src))


def intro_lines(n: int) -> int:
    """Số dòng đầu coi là phần chào/giới thiệu (không dùng cho hook): 10%, ít nhất 3 — chỉ khi video đủ dài."""
    skip = max(3, n // 10)
    return skip if n >= 3 * skip else 0


def chunks(lines: list[Line], size: int = BODY_CHUNK) -> list[list[Line]]:
    usable = [ln for ln in lines if ln.vi or ln.text]
    return [usable[i:i + size] for i in range(0, len(usable), size)]


def assemble(hook: tuple[str, tuple[int, int] | None, str], body: list[tuple[int, int, str]]) -> list[ScriptPart]:
    text, src, cta = hook
    parts = [ScriptPart(0, "hook", text, src)]
    for a, b, t in body:
        parts.append(ScriptPart(len(parts), "body", t, (a, b)))
    parts.append(ScriptPart(len(parts), "cta", cta, None))
    return parts


def script_text(parts: list[ScriptPart]) -> str:
    """Nội dung kịch bản.txt: mỗi đoạn một khối, cách nhau dòng trống — dán thẳng vào bước giọng đọc."""
    return "\n\n".join(p.text for p in parts) + "\n"


_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()
