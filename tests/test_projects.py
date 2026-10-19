from __future__ import annotations

import pytest

from pas.core.errors import InvalidInput
from pas.projects.repository import ProjectRepository


@pytest.fixture
def repo(db):
    return ProjectRepository(db)


def test_create_list_update_delete(repo: ProjectRepository, tmp_path):
    p = repo.create("  Phim  ngắn   tháng 10 ", str(tmp_path / "vào"), str(tmp_path / "ra"))
    assert p.name == "Phim ngắn tháng 10"
    assert [x.id for x in repo.list()] == [p.id]
    p2 = repo.rename(p.id, "Phim dài 电影")
    assert p2.name == "Phim dài 电影" and p2.input_dir == p.input_dir
    p3 = repo.update(p.id, output_dir="")
    assert p3.output_dir == "" and p3.name == "Phim dài 电影"
    repo.delete(p.id)
    assert repo.list() == []


def test_folders_untouched_on_delete(repo: ProjectRepository, tmp_path):
    folder = tmp_path / "Thư mục gốc"
    folder.mkdir()
    (folder / "video.mp4").write_bytes(b"x")
    p = repo.create("Dự án", str(folder))
    repo.delete(p.id)
    assert (folder / "video.mp4").exists()


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_invalid_names(repo: ProjectRepository, name):
    with pytest.raises(InvalidInput):
        repo.create(name)


@pytest.mark.parametrize("other", ["dự án a", "DỰ ÁN A", "  Dự   án A "])
def test_duplicate_name_case_insensitive_unicode(repo: ProjectRepository, other):
    # SQLite NOCASE chỉ gộp hoa/thường ASCII; tiếng Việt phải được xử lý ở tầng ứng dụng.
    repo.create("Dự án A")
    with pytest.raises(InvalidInput):
        repo.create(other)


def test_rename_to_existing_name(repo: ProjectRepository):
    repo.create("Một")
    b = repo.create("Hai")
    with pytest.raises(InvalidInput):
        repo.rename(b.id, "Một")


def test_update_missing_project(repo: ProjectRepository):
    with pytest.raises(InvalidInput):
        repo.update(999, name="x")


def test_nfd_name_matches_nfc(repo: ProjectRepository):
    import unicodedata
    repo.create("Phụ đề")
    with pytest.raises(InvalidInput):
        repo.create(unicodedata.normalize("NFD", "Phụ đề"))
