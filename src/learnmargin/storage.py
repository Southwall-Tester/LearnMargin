"""Small local workspace with atomic metadata writes and strict path boundaries."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Document


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        for folder in ("documents", "jobs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def directory(self, category: str, item_id: str) -> Path:
        if category not in {"documents", "jobs"} or not re.fullmatch(r"[a-f0-9]{32}", item_id):
            raise ValueError("无效的材料或任务编号。")
        return self.root / category / item_id

    def document(self, item_id: str) -> Document:
        path = self.directory("documents", item_id) / "document.json"
        if not path.is_file():
            raise FileNotFoundError("找不到这份材料，请重新导入。")
        return Document.model_validate_json(path.read_text(encoding="utf-8"))

    def save_document(self, document: Document) -> None:
        atomic_json(self.directory("documents", document.id) / "document.json", document.model_dump())

    def delete_document(self, item_id: str) -> None:
        target = self.directory("documents", item_id).resolve()
        if target.parent != (self.root / "documents").resolve():
            raise ValueError("材料路径无效。")
        if target.exists():
            shutil.rmtree(target)

    def save_job(self, job: dict) -> None:
        atomic_json(self.directory("jobs", job["id"]) / "job.json", job)

    def job(self, item_id: str) -> dict:
        path = self.directory("jobs", item_id) / "job.json"
        if not path.is_file():
            raise FileNotFoundError("找不到这项生成任务。")
        return json.loads(path.read_text(encoding="utf-8"))

    def jobs(self) -> list[dict]:
        result = []
        for path in (self.root / "jobs").glob("*/job.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda job: job["created_at"], reverse=True)

    def recover_interrupted(self) -> None:
        for job in self.jobs():
            if job["status"] in {"queued", "running"}:
                job.update(status="failed", stage="任务已中断", error="应用曾退出或重新启动，请重新生成。")
                self.save_job(job)
