"""Local-only HTTP application. Files stay local until an API job is requested."""
from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import data_directory, default_api, resolve_api
from .demo import demo_lesson
from .extraction_worker import extract_in_worker
from .http_security import LocalRequestSecurity
from .ingestion import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
    find_libreoffice,
    local_office_enabled,
)
from .models import APIConfig, ConnectionTestResult, GenerateRequest
from .office_sandbox import sandbox_backend
from .pipeline import generate_lesson, parse_range
from .provider import Provider
from .storage import Store, atomic_json, new_id, now

ARTIFACTS = {"lesson.pdf": "application/pdf", "lesson.html": "text/html", "lesson.json": "application/json",
             "sources.zip": "application/zip", "validation.json": "application/json"}


def document_summary(document):
    return {"id": document.id, "name": document.name, "kind": document.kind,
            "unit_label": document.unit_label, "total_units": len(document.units), "warnings": document.warnings,
            "units": [{"index": u.index, "label": u.label, "preview": u.text[:300],
                       "has_images": bool(u.image_paths)} for u in document.units]}


def job_summary(job):
    return {key: value for key, value in job.items() if key not in {"request"}}


def safe_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return safe_error(error.exceptions[0])
    if isinstance(error, (ValueError, RuntimeError)):
        return str(error)[:600]
    return "生成未完成。请检查浏览器渲染依赖、材料格式和 API 配置后重试。"


class Jobs:
    def __init__(self, store: Store):
        self.store = store
        self.tasks: dict[str, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(2)

    def submit(self, request: GenerateRequest | None = None) -> dict:
        if sum(not task.done() for task in self.tasks.values()) >= 6:
            raise ValueError("当前任务较多，请等待已有任务完成。")
        job = dict(id=new_id(), status="queued", stage="等待生成", progress=0, created_at=now(),
                   error=None, title=None, page_count=None, selection=[], warnings=[], artifacts={},
                   demo=request is None)
        if request:
            job["request"] = request.model_dump(mode="json", exclude={"api": {"api_key"}})
        self.store.save_job(job)
        task = asyncio.create_task(self.run(job, request))
        self.tasks[job["id"]] = task
        task.add_done_callback(lambda _: self.tasks.pop(job["id"], None))
        return job_summary(job)

    async def run(self, job: dict, request: GenerateRequest | None):
        def progress(stage: str, amount: int):
            job.update(stage=stage, progress=amount)
            self.store.save_job(job)

        try:
            async with self.slots:
                from .rendering import render_lesson
                job["status"] = "running"
                progress("准备材料", 5)
                output = self.store.directory("jobs", job["id"])
                if request is None:
                    lesson = demo_lesson()
                    usage = []
                else:
                    documents = [self.store.document(item_id) for item_id in request.document_ids]
                    async with Provider(request.api) as provider:
                        lesson = await generate_lesson(request, documents, self.store, output, provider, progress)
                        usage = provider.usage
                job.update(title=lesson.title, selection=[source.model_dump() for source in lesson.sources],
                           warnings=lesson.warnings)
                progress("排版并检查 PDF", 86)
                result = await render_lesson(lesson, output, layout=request.layout if request else "a4")
                atomic_json(output / "generation.json", {"version": __version__, "demo": request is None,
                            "api_usage": usage, "layout": request.layout if request else "a4"})
                with ZipFile(output / "sources.zip", "w", ZIP_DEFLATED) as archive:
                    for name in ("lesson.html", "lesson.json", "validation.json", "plan.json", "selection.json",
                                 "scope-reasoning.json", "section-source-review.json", "transcription.json",
                                 "generation.json"):
                        if (output / name).is_file():
                            archive.write(output / name, name)
                base = f"/api/jobs/{job['id']}/artifacts/"
                job.update(status="completed", stage="生成完成", progress=100,
                           page_count=result["page_count"], artifacts={"pdf": base + "lesson.pdf",
                           "html": base + "lesson.html", "json": base + "lesson.json", "source_zip": base + "sources.zip"})
                self.store.save_job(job)
        except asyncio.CancelledError:
            job.update(status="cancelled", stage="已取消", error=None)
            self.store.save_job(job)
            raise
        except Exception as error:
            failed_stage = job["stage"]
            job.update(status="failed", stage="生成失败", failed_stage=failed_stage,
                       error=f"{failed_stage}：{safe_error(error)}")
            self.store.save_job(job)
        finally:
            # The request (including its SecretStr) is not retained by the job registry.
            request = None

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def create_app(root: Path | None = None) -> FastAPI:
    store = Store(root or data_directory())
    jobs = Jobs(store)
    extraction_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_):
        store.recover_interrupted()
        yield
        await jobs.close()

    app = FastAPI(title="LearnMargin", version=__version__, lifespan=lifespan)
    app.state.store, app.state.jobs = store, jobs
    app.add_middleware(LocalRequestSecurity)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, error):
        allowed = set(GenerateRequest.model_fields) | set(APIConfig.model_fields) | {
            "scope", "mode", "ranges", "topics", "include_prerequisites", "file",
            "item_id", "job_id", "document_id", "index", "name",
        }
        fields = ", ".join(".".join(str(part) if isinstance(part, int) or part in allowed
                                   else "[未定义字段]" for part in item["loc"][1:8])
                           for item in error.errors()[:6])
        return JSONResponse({"detail": f"输入格式不正确，请检查：{fields or '请求参数'}。"}, status_code=422)

    @app.exception_handler(RecursionError)
    async def excessively_nested_request(_, error):
        return JSONResponse({"detail": "请求内容嵌套过深，请简化后重试。"}, status_code=400)

    @app.exception_handler(ValueError)
    async def invalid_value(_, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(_, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/settings")
    async def settings():
        configured = resolve_api(default_api())
        values = configured.model_dump(exclude={"api_key", "timeout_seconds"})
        values["has_api_key"] = bool(configured.api_key.get_secret_value())
        office = find_libreoffice()
        isolation = sandbox_backend(office)
        browser = False
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser = Path(playwright.chromium.executable_path).is_file()
        except Exception:
            pass
        return {"api": values, "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // 1024 // 1024, "max_documents": 8},
                "formats": sorted(SUPPORTED_EXTENSIONS), "capabilities": {
                    "libreoffice": local_office_enabled() and isolation is not None,
                    "office_sandbox": isolation,
                    "local_office_enabled": local_office_enabled(), "browser": browser},
                "demo_available": True}

    @app.post("/api/connection-test", response_model=ConnectionTestResult)
    async def connection_test(config: APIConfig):
        config = resolve_api(config)
        hostname = urlsplit(config.base_url).hostname
        if not config.api_key.get_secret_value() and hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("请先为当前 API 服务填写密钥。")
        async with Provider(config) as provider:
            return await provider.test_connection()

    @app.post("/api/documents", status_code=201, openapi_extra={"requestBody": {
        "required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
        }}},
    }})
    async def import_document(request: Request):
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise ValueError("请上传一份材料文件。")
            return await save_upload(file)

    async def save_upload(file: UploadFile):
        original = (file.filename or "material").replace("\\", "/").split("/")[-1][:180]
        extension = Path(original).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("不支持这个文件格式，请导入 PDF、PPTX、DOCX、文本、电子书或图片。")
        item_id = new_id()
        folder = store.directory("documents", item_id)
        folder.mkdir(parents=True)
        path = folder / ("source" + extension)
        total = 0
        try:
            with path.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError("单份材料不能超过 50 MB，请拆分后导入。")
                    target.write(chunk)
            async with extraction_lock:
                document = await extract_in_worker(path, folder, item_id, original)
            store.save_document(document)
            return document_summary(document)
        except BaseException:
            store.delete_document(item_id)
            raise

    @app.get("/api/documents/{item_id}")
    async def get_document(item_id: str):
        return document_summary(store.document(item_id))

    @app.get("/api/documents/{item_id}/units/{index}")
    async def unit_detail(item_id: str, index: int):
        document = store.document(item_id)
        if not 1 <= index <= len(document.units):
            raise HTTPException(404, "找不到这个内容单元。")
        unit = document.units[index - 1]
        return {"index": unit.index, "label": unit.label, "text": unit.text,
                "images": [f"/api/documents/{item_id}/images/{quote(name, safe='')}" for name in unit.image_paths]}

    @app.get("/api/documents/{item_id}/images/{name:path}")
    async def unit_image(item_id: str, name: str):
        document = store.document(item_id)
        valid = {image for unit in document.units for image in unit.image_paths}
        folder = store.directory("documents", item_id).resolve()
        path = (folder / name).resolve()
        if name not in valid or not path.is_relative_to(folder) or not path.is_file():
            raise HTTPException(404, "找不到图片。")
        if path.suffix.lower() != ".png":
            raise HTTPException(404, "找不到图片。")
        return FileResponse(path, media_type="image/png")

    @app.delete("/api/documents/{item_id}")
    async def remove_document(item_id: str):
        for job in store.jobs():
            if job["status"] in {"queued", "running"} and item_id in job.get("request", {}).get("document_ids", []):
                raise HTTPException(409, "这份材料正在生成讲义，请先取消任务。")
        store.document(item_id)
        store.delete_document(item_id)
        return {"deleted": True}

    @app.post("/api/jobs", status_code=202)
    async def generate(request: GenerateRequest):
        if request.reading_mode == "handwritten" and not request.api.vision:
            raise ValueError("手写讲义需要视觉输入，请启用支持图片的多模态模型。")
        if len(set(request.document_ids)) != len(request.document_ids):
            raise ValueError("同一份材料不能重复选择。")
        if request.scope.mode == "pages":
            if not request.scope.ranges:
                raise ValueError("请为至少一份主材料指定页码范围。")
            if set(request.scope.ranges) - set(request.document_ids):
                raise ValueError("页码范围包含未选中的材料，请重新选择。")
        for item_id in request.document_ids:
            document = store.document(item_id)
            if request.scope.mode == "pages" and item_id in request.scope.ranges:
                parse_range(request.scope.ranges[item_id], len(document.units))
        if request.scope.mode == "topics" and not request.scope.topics.strip():
            raise ValueError("请填写知识点范围。")
        request.api = resolve_api(request.api)
        hostname = urlsplit(request.api.base_url).hostname
        if not request.api.api_key.get_secret_value() and hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("请先为当前 API 服务填写密钥。")
        return jobs.submit(request)

    @app.post("/api/demo", status_code=202)
    async def demo():
        return jobs.submit()

    @app.get("/api/jobs")
    async def list_jobs():
        return [job_summary(job) for job in store.jobs()]

    @app.get("/api/jobs/{item_id}")
    async def get_job(item_id: str):
        return job_summary(store.job(item_id))

    @app.get("/api/jobs/{job_id}/sources/{document_id}/{index}")
    async def job_source(job_id: str, document_id: str, index: int):
        job = store.job(job_id)
        ref = f"{document_id}:{index}"
        if job["status"] != "completed" or ref not in {item["ref"] for item in job.get("selection", [])}:
            raise HTTPException(404, "这份来源不在当前讲义的引用范围内。")
        detail = await unit_detail(document_id, index)
        detail["transcription"] = None
        path = store.directory("jobs", job_id) / "transcription.json"
        if path.resolve() != path:
            raise HTTPException(404, "来源文件路径无效。")
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            record = next((unit for unit in data.get("units", []) if unit["ref"] == ref), None)
            if record:
                detail["transcription"] = {"text": record["text"], "uncertainties": record["uncertainties"]}
        return detail

    @app.post("/api/jobs/{item_id}/cancel")
    async def cancel(item_id: str):
        job = store.job(item_id)
        task = jobs.tasks.get(item_id)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # A queued coroutine may be cancelled before its try/finally starts.
            job = store.job(item_id)
            if job["status"] in {"queued", "running"}:
                job.update(status="cancelled", stage="已取消")
                store.save_job(job)
        return job_summary(store.job(item_id))

    @app.get("/api/jobs/{item_id}/artifacts/{name}")
    async def artifact(item_id: str, name: str):
        job = store.job(item_id)
        if job["status"] != "completed" or name not in ARTIFACTS:
            raise HTTPException(404, "文件尚未生成或不存在。")
        path = store.directory("jobs", item_id) / name
        if path.resolve() != path or not path.is_file():
            raise HTTPException(404, "生成文件已被移除，请重新生成。")
        return FileResponse(path, media_type=ARTIFACTS[name], filename=name,
                            content_disposition_type="inline" if name in {"lesson.pdf", "lesson.html"} else "attachment")

    web = Path(__file__).parent / "web"
    if not web.joinpath("index.html").is_file():
        web = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if web.joinpath("index.html").is_file():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    else:
        @app.get("/")
        async def frontend_missing():
            return JSONResponse({"detail": "前端尚未构建，请在 frontend 中运行 npm ci 和 npm run build。"}, status_code=503)
    return app
