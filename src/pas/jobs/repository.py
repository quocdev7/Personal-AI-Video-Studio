from __future__ import annotations

import json
from dataclasses import dataclass

from pas.core.db import Database, utcnow
from pas.jobs.models import ACTIVE_STATUSES, RETRYABLE_STATUSES, Job, JobStatus


@dataclass(frozen=True)
class JobLogEntry:
    ts: str
    level: str
    message: str


_UPDATABLE = {
    "status", "progress", "message", "error", "error_hint", "result_json",
    "started_at", "finished_at", "attempts",
}


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, kind: str, title: str, params: dict, project_id: int | None = None) -> Job:
        cur = self.db.execute(
            "INSERT INTO jobs (project_id, kind, title, params_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, kind, title, json.dumps(params, ensure_ascii=False), JobStatus.QUEUED, utcnow()),
        )
        return self.get(cur.lastrowid)

    def get(self, job_id: int) -> Job | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return Job.from_row(row) if row else None

    def list(self, statuses: set[JobStatus] | frozenset[JobStatus] | None = None, limit: int = 500,
             project_id: int | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs"
        where, params = [], []
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(str(s) for s in statuses)
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [Job.from_row(r) for r in self.db.query(sql, tuple(params))]

    def update(self, job_id: int, **fields) -> None:
        if "result" in fields:
            result = fields.pop("result")
            fields["result_json"] = json.dumps(result, ensure_ascii=False) if result is not None else None
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Trường không cập nhật được: {unknown}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))

    def transition(self, job_id: int, from_status: JobStatus, to_status: JobStatus) -> bool:
        cur = self.db.execute(
            "UPDATE jobs SET status = ? WHERE id = ? AND status = ?", (to_status, job_id, from_status)
        )
        return cur.rowcount == 1

    def claim_next(self) -> Job | None:
        """Lấy tác vụ chờ lâu nhất và chuyển sang RUNNING một cách nguyên tử."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY id LIMIT 1", (JobStatus.QUEUED,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ?, finished_at = NULL, attempts = attempts + 1, "
                "progress = 0, message = '', error = NULL, error_hint = NULL WHERE id = ?",
                (JobStatus.RUNNING, utcnow(), row["id"]),
            )
        return self.get(row["id"])

    def cancel_if_queued(self, job_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, message = 'Đã hủy trước khi chạy' WHERE id = ? AND status = ?",
            (JobStatus.CANCELLED, utcnow(), job_id, JobStatus.QUEUED),
        )
        return cur.rowcount == 1

    def requeue(self, job_id: int) -> bool:
        statuses = tuple(str(s) for s in RETRYABLE_STATUSES)
        cur = self.db.execute(
            f"UPDATE jobs SET status = ?, progress = 0, message = '', error = NULL, error_hint = NULL, "
            f"started_at = NULL, finished_at = NULL WHERE id = ? AND status IN ({','.join('?' * len(statuses))})",
            (JobStatus.QUEUED, job_id, *statuses),
        )
        return cur.rowcount == 1

    def mark_interrupted(self) -> list[int]:
        """Gọi khi khởi động: tác vụ còn RUNNING/PAUSED nghĩa là lần trước app tắt bất thường."""
        rows = self.db.query(
            "SELECT id FROM jobs WHERE status IN (?, ?)", (JobStatus.RUNNING, JobStatus.PAUSED)
        )
        ids = [r["id"] for r in rows]
        if ids:
            self.db.execute(
                f"UPDATE jobs SET status = ?, finished_at = ?, "
                f"message = 'Ứng dụng đã đóng khi tác vụ đang chạy' WHERE id IN ({','.join('?' * len(ids))})",
                (JobStatus.INTERRUPTED, utcnow(), *ids),
            )
        return ids

    def count_by_status(self) -> dict[JobStatus, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {JobStatus(r["status"]): r["n"] for r in rows}

    def active(self) -> list[Job]:
        return self.list(ACTIVE_STATUSES)

    def add_log(self, job_id: int, level: str, message: str) -> None:
        self.db.execute(
            "INSERT INTO job_logs (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, utcnow(), level, message),
        )

    def logs(self, job_id: int) -> list[JobLogEntry]:
        rows = self.db.query("SELECT ts, level, message FROM job_logs WHERE job_id = ? ORDER BY id", (job_id,))
        return [JobLogEntry(r["ts"], r["level"], r["message"]) for r in rows]
