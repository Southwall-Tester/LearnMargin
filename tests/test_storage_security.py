from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from learnmargin import storage
from learnmargin.models import Document, SourceUnit
from learnmargin.storage import Store, atomic_json

ITEM_ID = "a" * 32


def directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def test_category_link_cannot_redirect_store_outside_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    directory_link(root / "documents", outside)
    with pytest.raises(ValueError, match="符号链接或重定向"):
        Store(root)
    assert list(outside.iterdir()) == []


def test_document_link_is_rejected_for_read_write_and_delete(tmp_path):
    store = Store(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    directory_link(store.root / "documents" / ITEM_ID, outside)
    document = Document(id=ITEM_ID, name="notes.txt", kind="txt", unit_label="段",
                        units=[SourceUnit(index=1, label="段", text="正文")])
    for operation in (lambda: store.document(ITEM_ID), lambda: store.save_document(document),
                      lambda: store.delete_document(ITEM_ID)):
        with pytest.raises(ValueError, match="符号链接或重定向"):
            operation()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (outside / "document.json").exists()


def test_job_enumeration_does_not_follow_links_or_mismatched_metadata(tmp_path):
    store = Store(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job.json").write_text(json.dumps({"id": ITEM_ID, "status": "running", "created_at": "2026"}))
    directory_link(store.root / "jobs" / ITEM_ID, outside)
    mismatch = store.directory("jobs", "b" * 32)
    mismatch.mkdir()
    (mismatch / "job.json").write_text(json.dumps({"id": ITEM_ID, "status": "running", "created_at": "2026"}))
    assert store.jobs() == []
    store.recover_interrupted()
    assert json.loads((outside / "job.json").read_text())["status"] == "running"


def test_metadata_file_link_is_rejected_for_read_and_write(tmp_path):
    store = Store(tmp_path / "data")
    folder = store.directory("jobs", ITEM_ID)
    folder.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("synthetic private data", encoding="utf-8")
    try:
        (folder / "job.json").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="元数据"):
        store.job(ITEM_ID)
    with pytest.raises(ValueError, match="元数据"):
        store.save_job({"id": ITEM_ID})
    assert outside.read_text(encoding="utf-8") == "synthetic private data"


def test_atomic_write_does_not_overwrite_or_delete_preexisting_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    temporary = tmp_path / "job.fixed.tmp"
    temporary.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        atomic_json(tmp_path / "job.json", {"id": ITEM_ID})
    assert temporary.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "job.json").exists()


def test_metadata_replace_waits_for_reader_even_across_store_instances(tmp_path, monkeypatch):
    reader = Store(tmp_path / "data")
    writer = Store(tmp_path / "data")
    reader.save_job({"id": ITEM_ID, "status": "queued", "created_at": "2026"})
    path = reader.directory("jobs", ITEM_ID) / "job.json"
    opened, release, replacing = Event(), Event(), Event()
    read_text, replace = Path.read_text, Path.replace

    def held_read(candidate, *args, **kwargs):
        if candidate != path:
            return read_text(candidate, *args, **kwargs)
        with candidate.open(encoding="utf-8") as handle:
            opened.set()
            assert release.wait(5)
            return handle.read()

    def observed_replace(candidate, target):
        if target == path:
            replacing.set()
        return replace(candidate, target)

    monkeypatch.setattr(Path, "read_text", held_read)
    monkeypatch.setattr(Path, "replace", observed_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader.job, ITEM_ID)
        try:
            assert opened.wait(2)
            writing = pool.submit(writer.save_job, {"id": ITEM_ID, "status": "ready", "created_at": "2026"})
            assert not replacing.wait(.1)
        finally:
            release.set()
        assert reading.result(timeout=2)["status"] == "queued"
        writing.result(timeout=2)
    assert replacing.is_set()
    assert reader.job(ITEM_ID)["status"] == "ready"


def test_atomic_write_reports_permission_errors_and_preserves_previous_metadata(tmp_path, monkeypatch):
    path = tmp_path / "job.json"
    atomic_json(path, {"status": "queued"})

    def denied(*args):
        raise PermissionError("synthetic access denied")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError, match="access denied"):
        atomic_json(path, {"status": "ready"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "queued"}
    assert list(tmp_path.glob("*.tmp")) == []
