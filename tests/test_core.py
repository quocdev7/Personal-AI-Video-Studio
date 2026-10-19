"""Paths, config, disk, DB, errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pas.core.config import Settings, load_settings, save_settings
from pas.core.db import Database
from pas.core.disk import ensure_free_space, free_bytes
from pas.core.errors import InsufficientDiskSpace, InvalidInput, PasError
from pas.core.paths import AppPaths, default_data_dir, ensure_not_source, unique_output_path


# ------------------------------------------------------------------ paths

def test_unique_output_path_never_overwrites(tmp_path: Path):
    target = tmp_path / "Tập 1 — phụ đề.srt"
    assert unique_output_path(target) == target
    target.write_text("x", encoding="utf-8")
    first = unique_output_path(target)
    assert first.name == "Tập 1 — phụ đề (1).srt"
    first.write_text("y", encoding="utf-8")
    assert unique_output_path(target).name == "Tập 1 — phụ đề (2).srt"
    assert target.read_text(encoding="utf-8") == "x"


def test_ensure_not_source_blocks_same_file(tmp_path: Path):
    src = tmp_path / "Video gốc.mp4"
    src.write_bytes(b"0")
    with pytest.raises(InvalidInput):
        ensure_not_source(tmp_path / "." / "VIDEO GỐC.mp4", src)  # khác hoa/thường, cùng file trên Windows
    ensure_not_source(tmp_path / "khác.mp4", src)


def test_app_paths_ensure_and_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAS_DATA_DIR", str(tmp_path / "dữ liệu"))
    paths = AppPaths(default_data_dir()).ensure()
    assert paths.root == tmp_path / "dữ liệu"
    for d in (paths.logs_dir, paths.temp_dir, paths.models_dir):
        assert d.is_dir()


# ------------------------------------------------------------------ config

def test_settings_roundtrip(tmp_path: Path):
    f = tmp_path / "settings.json"
    s = Settings(ffmpeg_path=r"C:\Công cụ\ffmpeg.exe", max_concurrent_jobs=2, keep_temp_files=True)
    save_settings(s, f)
    assert load_settings(f) == s
    assert not f.with_suffix(".json.tmp").exists()


def test_settings_missing_file_gives_defaults(tmp_path: Path):
    assert load_settings(tmp_path / "không có.json") == Settings()


@pytest.mark.parametrize("content", ["{không phải json", "[1, 2]", ""])
def test_settings_corrupt_file_is_backed_up(tmp_path: Path, content: str):
    f = tmp_path / "settings.json"
    f.write_text(content, encoding="utf-8")
    assert load_settings(f) == Settings()
    backups = list(tmp_path.glob("settings.corrupt-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == content
    assert not f.exists()


def test_settings_wrong_types_and_ranges(tmp_path: Path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"max_concurrent_jobs": 99, "min_free_disk_gb": 5, "keep_temp_files": "yes",
                             "ffmpeg_path": 123, "unknown": 1}), encoding="utf-8")
    s = load_settings(f)
    assert s.max_concurrent_jobs == 4
    assert s.min_free_disk_gb == 5.0
    assert s.keep_temp_files is False
    assert s.ffmpeg_path == ""


def test_settings_bool_is_not_int(tmp_path: Path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"max_concurrent_jobs": True}), encoding="utf-8")
    assert load_settings(f).max_concurrent_jobs == 1


# ------------------------------------------------------------------ disk

def test_free_bytes_for_missing_subdir(tmp_path: Path):
    assert free_bytes(tmp_path / "chưa tạo" / "sâu hơn") > 0


def test_ensure_free_space_raises_with_hint(tmp_path: Path):
    ensure_free_space(tmp_path, 1)
    with pytest.raises(InsufficientDiskSpace) as ei:
        ensure_free_space(tmp_path, 10**18)
    assert ei.value.hint


# ------------------------------------------------------------------ db

def test_db_schema_and_reopen(tmp_path: Path):
    path = tmp_path / "Cơ sở dữ liệu" / "app.db"
    path.parent.mkdir()
    db = Database(path)
    assert db.schema_version >= 1
    db.execute("INSERT INTO projects (name, created_at, updated_at) VALUES ('a', 'x', 'x')")
    db.close()
    db2 = Database(path)
    assert db2.schema_version >= 1
    assert db2.query_one("SELECT COUNT(*) AS n FROM projects")["n"] == 1
    db2.close()


def test_db_transaction_rolls_back(db: Database):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO projects (name, created_at, updated_at) VALUES ('b', 'x', 'x')")
            raise RuntimeError("boom")
    assert db.query_one("SELECT COUNT(*) AS n FROM projects")["n"] == 0


def test_pas_error_str():
    assert str(PasError("Lỗi", "Làm thế này")) == "Lỗi — Làm thế này"
    assert str(PasError("Lỗi")) == "Lỗi"
