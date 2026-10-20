"""Khởi tạo và kết nối toàn bộ dịch vụ. UI chỉ làm việc với AppServices."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pas.core.config import Settings, load_settings, save_settings
from pas.core.db import Database
from pas.core.paths import AppPaths, default_data_dir
from pas.jobs.manager import JobManager
from pas.jobs.registry import TaskRegistry
from pas.jobs.repository import JobRepository
from pas.media.ffmpeg import FFmpegTools, resolve_tools
from pas.modules import register_all
from pas.projects.repository import ProjectRepository

log = logging.getLogger(__name__)


@dataclass
class AppServices:
    paths: AppPaths
    settings: Settings
    db: Database
    projects: ProjectRepository
    job_repo: JobRepository
    registry: TaskRegistry
    jobs: JobManager

    def ffmpeg_tools(self) -> FFmpegTools:
        """Đọc cấu hình tại thời điểm gọi để thay đổi trong Cài đặt có hiệu lực ngay."""
        return resolve_tools(self.settings.ffmpeg_path, self.settings.ffprobe_path)

    def save_settings(self) -> None:
        self.settings.validated()
        save_settings(self.settings, self.paths.settings_file)
        self.jobs.keep_temp = self.settings.keep_temp_files
        self.jobs.max_workers = self.settings.max_concurrent_jobs

    def close(self) -> None:
        self.jobs.shutdown()
        self.db.close()


def build_services(data_dir: Path | None = None, start_jobs: bool = True) -> AppServices:
    paths = AppPaths(Path(data_dir) if data_dir else default_data_dir()).ensure()
    settings = load_settings(paths.settings_file)
    db = Database(paths.db_file)
    job_repo = JobRepository(db)
    registry = TaskRegistry()
    jobs = JobManager(job_repo, registry, paths.temp_dir,
                      max_workers=settings.max_concurrent_jobs, keep_temp=settings.keep_temp_files)
    services = AppServices(paths, settings, db, ProjectRepository(db), job_repo, registry, jobs)
    register_all(registry, services.ffmpeg_tools, lambda: services.settings, paths)
    if start_jobs:
        jobs.start()
    log.info("Dữ liệu ứng dụng: %s", paths.root)
    return services
