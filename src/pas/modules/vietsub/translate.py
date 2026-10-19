"""Dịch Trung → Việt bằng LLM chạy cục bộ qua Ollama (mặc định Qwen2.5 7B, Apache-2.0).

- Chỉ gọi tới Ollama trên máy này (127.0.0.1); không gửi dữ liệu ra ngoài, không tốn phí.
- Ứng dụng không tự tải mô hình: thiếu mô hình → báo lỗi kèm lệnh tải và dung lượng để người dùng tự quyết.
- Mỗi lần gửi một lô câu, ép đầu ra theo JSON schema và giới hạn số token (tránh mô hình sinh lặp vô hạn).
  Câu thiếu hoặc còn sót chữ Trung được dịch lại riêng; vẫn hỏng thì báo là chưa dịch được, không bịa.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pas.core.errors import DependencyMissing, ExternalToolError, JobCancelled
from pas.modules.vietsub.ocr import has_cjk

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b"
MODEL_INFO = {"qwen2.5:7b": "~4,7 GB, giấy phép Apache-2.0"}
BATCH_SIZE = 8
REQUEST_TIMEOUT = 600.0

SYSTEM_PROMPT = (
    "Bạn là người dịch phụ đề video từ tiếng Trung sang tiếng Việt. Dịch tự nhiên, ngắn gọn như phụ đề, đúng nghĩa, "
    "xưng hô phù hợp ngữ cảnh. Các câu được đánh số theo thứ tự trong video. Không giữ lại chữ Hán nào trong bản dịch. "
    'Trả về JSON {"items":[{"id":số,"vi":"bản dịch"}]} với đủ mọi id đầu vào.'
)
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}}, "required": ["id", "vi"],
    }}},
    "required": ["items"],
}


def install_hint(model: str) -> str:
    size = MODEL_INFO.get(model, "xem dung lượng trên ollama.com/library")
    return (f"Tải mô hình bằng lệnh `ollama pull {model}` ({size}). "
            "Chưa có Ollama: `winget install Ollama.Ollama` (miễn phí, MIT).")


@dataclass
class Translation:
    vi: dict[int, str]
    failed: list[int]


class OllamaClient:
    def __init__(self, url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout: float = REQUEST_TIMEOUT):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _request(self, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def ensure_ready(self) -> None:
        """Ollama đang chạy và đã có mô hình; ngược lại ném DependencyMissing với hướng dẫn cụ thể."""
        try:
            tags = self._request("/api/tags", timeout=5.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DependencyMissing(
                f"Không kết nối được Ollama tại {self.url} ({_reason(exc)}).",
                "Mở ứng dụng Ollama (biểu tượng ở khay hệ thống) rồi chạy lại. " + install_hint(self.model),
            ) from exc
        names = {m.get("name", "") for m in tags.get("models", [])} | {m.get("model", "") for m in tags.get("models", [])}
        wanted = self.model if ":" in self.model else self.model + ":latest"
        if wanted not in names:
            raise DependencyMissing(f"Ollama chưa có mô hình '{self.model}'.", install_hint(self.model))

    def chat_json(self, user: str, num_predict: int, temperature: float = 0.2,
                  ctx: "JobContext | None" = None) -> dict:
        body = {
            "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": "10m",
            "options": {"temperature": temperature, "num_predict": num_predict},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        }
        result: dict = {}

        def _call():
            try:
                result["ok"] = self._request("/api/chat", body, timeout=self.timeout)
            except Exception as exc:  # chuyển sang luồng chính để báo lỗi đúng chỗ
                result["err"] = exc

        th = threading.Thread(target=_call, name="ollama-chat", daemon=True)
        th.start()
        while th.is_alive():
            th.join(timeout=0.2)
            if ctx is not None and ctx.is_cancelled:
                # Không ngắt được urllib từ luồng khác; num_predict giới hạn phần việc thừa của Ollama.
                raise JobCancelled()
        if "err" in result:
            exc = result["err"]
            if isinstance(exc, (urllib.error.URLError, OSError)):
                raise ExternalToolError(f"Gọi Ollama thất bại ({_reason(exc)}).",
                                        "Kiểm tra Ollama còn chạy. Máy yếu có thể cần đóng bớt ứng dụng khác.") from exc
            raise ExternalToolError(f"Ollama trả về dữ liệu lỗi: {exc}", "Xem log tác vụ.") from exc
        content = (result["ok"].get("message") or {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError:
            log.warning("Ollama trả JSON hỏng: %r", content[:500])
            return {}


def _reason(exc: Exception) -> str:
    return str(getattr(exc, "reason", None) or exc)


def build_prompt(lines: dict[int, str]) -> str:
    return "\n".join(f"{i}: {t}" for i, t in lines.items())


def parse_items(data: dict, wanted: set[int]) -> dict[int, str]:
    """Lấy bản dịch hợp lệ: id nằm trong lô, chuỗi không rỗng, không còn chữ Hán."""
    out: dict[int, str] = {}
    items = data.get("items") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        i, vi = item.get("id"), item.get("vi")
        if isinstance(i, bool) or not isinstance(i, int) or i not in wanted or not isinstance(vi, str):
            continue
        vi = " ".join(vi.split())
        if vi and not has_cjk(vi):
            out[i] = vi
    return out


def translate_lines(
    client: OllamaClient,
    lines: dict[int, str],
    done: dict[int, str] | None = None,
    ctx: "JobContext | None" = None,
    on_batch: Callable[[dict[int, str]], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Translation:
    """Dịch theo lô. `done`: bản dịch đã có từ lần chạy trước (bỏ qua). `on_batch` được gọi sau mỗi lô để lưu tiến độ."""
    vi = dict(done or {})
    todo = [i for i in lines if i not in vi]
    failed: list[int] = []
    total = max(1, len(todo))
    finished = 0
    for b in range(0, len(todo), BATCH_SIZE):
        if ctx is not None:
            ctx.checkpoint()
        batch = {i: lines[i] for i in todo[b:b + BATCH_SIZE]}
        got = parse_items(client.chat_json(build_prompt(batch), _budget(batch), ctx=ctx), set(batch))
        for i in batch:
            if i in got:
                continue
            if ctx is not None:
                ctx.checkpoint()
            one = parse_items(client.chat_json(build_prompt({i: batch[i]}), _budget({i: batch[i]}), 0.5, ctx), {i})
            if i in one:
                got[i] = one[i]
            else:
                failed.append(i)
                log.warning("Không dịch được câu %s: %r", i, batch[i])
        vi.update(got)
        finished += len(batch)
        if on_batch:
            on_batch(got)
        if on_progress:
            on_progress(finished / total)
    return Translation(vi={i: vi[i] for i in lines if i in vi}, failed=failed)


def _budget(batch: dict[int, str]) -> int:
    # ~3 token tiếng Việt cho mỗi chữ Hán, cộng phần khung JSON; đủ rộng nhưng chặn được sinh lặp.
    return 64 + sum(16 + 4 * len(t) for t in batch.values())
