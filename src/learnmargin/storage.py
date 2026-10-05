"""Small local workspace with atomic metadata writes and strict path boundaries."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .models import Document

_METADATA_LOCK = RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def atomic_json(path: Path, value: dict) -> None:
    # Windows refuses a replace while another thread has the destination open.
    # Use the same lock for metadata reads, including across Store instances.
    with _METADATA_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        created = False
        try:
            with temporary.open("x", encoding="utf-8") as output:
                created = True
                json.dump(value, output, ensure_ascii=False, indent=2)
            temporary.replace(path)
        finally:
            if created:
                temporary.unlink(missing_ok=True)


def _read_metadata(path: Path) -> str:
    with _METADATA_LOCK:
        return path.read_text(encoding="utf-8")


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        for folder in ("documents", "jobs"):
            self._category(folder).mkdir(parents=True, exist_ok=True)

    def _category(self, category: str) -> Path:
        if category not in {"documents", "jobs"}:
            raise ValueError("无效的材料或任务编号。")
        path = self.root / category
        if path.resolve() != path:
            raise ValueError("数据目录包含符号链接或重定向，无法安全访问。")
        return path

    def directory(self, category: str, item_id: str) -> Path:
        if category not in {"documents", "jobs"} or not re.fullmatch(r"[a-f0-9]{32}", item_id):
            raise ValueError("无效的材料或任务编号。")
        path = self._category(category) / item_id
        if path.resolve() != path:
            raise ValueError("材料或任务目录包含符号链接或重定向，无法安全访问。")
        return path

    def _metadata(self, category: str, item_id: str, name: str) -> Path:
        path = self.directory(category, item_id) / name
        if path.resolve() != path:
            raise ValueError("材料或任务元数据包含符号链接或重定向，无法安全访问。")
        return path

    def document(self, item_id: str) -> Document:
        path = self._metadata("documents", item_id, "document.json")
        if not path.is_file():
            raise FileNotFoundError("找不到这份材料，请重新导入。")
        document = Document.model_validate_json(_read_metadata(path))
        if document.id != item_id:
            raise ValueError("材料元数据编号与目录不一致。")
        return document

    def save_document(self, document: Document) -> None:
        atomic_json(self._metadata("documents", document.id, "document.json"), document.model_dump())

    def delete_document(self, item_id: str) -> None:
        target = self.directory("documents", item_id).resolve()
        if target.parent != (self.root / "documents").resolve():
            raise ValueError("材料路径无效。")
        if target.exists():
            shutil.rmtree(target)

    def save_job(self, job: dict) -> None:
        atomic_json(self._metadata("jobs", job["id"], "job.json"), job)

    def job(self, item_id: str) -> dict:
        path = self._metadata("jobs", item_id, "job.json")
        if not path.is_file():
            raise FileNotFoundError("找不到这项生成任务。")
        job = json.loads(_read_metadata(path))
        if not isinstance(job, dict) or job.get("id") != item_id:
            raise ValueError("任务元数据编号与目录不一致。")
        return job

    def jobs(self) -> list[dict]:
        result = []
        for path in self._category("jobs").iterdir():
            try:
                job = self.job(path.name)
                if isinstance(job.get("created_at"), str) and isinstance(job.get("status"), str):
                    result.append(job)
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda job: job["created_at"], reverse=True)

    def recover_interrupted(self) -> None:
        for job in self.jobs():
            if job["status"] in {"queued", "running"}:
                job.update(status="failed", stage="任务已中断", error="应用曾退出或重新启动，请重新生成。")
                self.save_job(job)
