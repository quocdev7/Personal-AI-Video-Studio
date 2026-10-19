from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # Ứng dụng tắt khi tác vụ đang chạy.

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]


STATUS_LABELS = {
    JobStatus.QUEUED: "Đang chờ",
    JobStatus.RUNNING: "Đang chạy",
    JobStatus.PAUSED: "Tạm dừng",
    JobStatus.COMPLETED: "Hoàn thành",
    JobStatus.FAILED: "Lỗi",
    JobStatus.CANCELLED: "Đã hủy",
    JobStatus.INTERRUPTED: "Bị gián đoạn",
}

ACTIVE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED})
FINISHED_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED})
RETRYABLE_STATUSES = frozenset({JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED})


@dataclass
class Job:
    id: int
    kind: str
    title: str
    status: JobStatus
    params: dict = field(default_factory=dict)
    project_id: int | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    error_hint: str | None = None
    result: dict | None = None
    attempts: int = 0
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        return cls(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            status=JobStatus(row["status"]),
            params=json.loads(row["params_json"] or "{}"),
            project_id=row["project_id"],
            progress=row["progress"],
            message=row["message"],
            error=row["error"],
            error_hint=row["error_hint"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            attempts=row["attempts"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @property
    def elapsed_seconds(self) -> float | None:
        if not self.started_at:
            return None
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.finished_at) if self.finished_at else datetime.now(start.tzinfo)
        return max(0.0, (end - start).total_seconds())


@dataclass(frozen=True)
class JobEvent:
    """Phát cho listener mỗi khi tác vụ đổi trạng thái/tiến trình."""

    job_id: int
    status: JobStatus
    progress: float
    message: str
