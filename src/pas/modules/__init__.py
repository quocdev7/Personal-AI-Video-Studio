"""Các module chức năng. Mỗi module đăng ký loại tác vụ của mình vào TaskRegistry."""

from __future__ import annotations

from typing import Callable

from pas.jobs.registry import TaskRegistry
from pas.media.ffmpeg import FFmpegTools


def register_all(registry: TaskRegistry, tools_provider: Callable[[], FFmpegTools]) -> None:
    from pas.modules.media_check import tasks as media_check_tasks

    media_check_tasks.register(registry, tools_provider)
