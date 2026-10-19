from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pas.core.errors import PasError

if TYPE_CHECKING:
    from pas.jobs.context import JobContext

Handler = Callable[["JobContext"], dict | None]
Validator = Callable[[dict], None]


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    display_name: str
    handler: Handler
    # Kiểm tra tham số khi gửi tác vụ; ném PasError nếu sai để người dùng biết ngay.
    validate: Validator | None = None


class TaskRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, TaskSpec] = {}

    def register(self, spec: TaskSpec) -> None:
        if spec.kind in self._specs:
            raise ValueError(f"Loại tác vụ '{spec.kind}' đã được đăng ký")
        self._specs[spec.kind] = spec

    def get(self, kind: str) -> TaskSpec:
        try:
            return self._specs[kind]
        except KeyError:
            raise PasError(
                f"Loại tác vụ không xác định: {kind}",
                "Module tạo ra tác vụ này có thể đã bị tắt hoặc gỡ. Hãy cập nhật ứng dụng.",
            ) from None

    def display_name(self, kind: str) -> str:
        spec = self._specs.get(kind)
        return spec.display_name if spec else kind

    def kinds(self) -> list[str]:
        return sorted(self._specs)
