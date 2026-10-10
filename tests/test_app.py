import asyncio
import json
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from learnmargin import rendering
from learnmargin.app import create_app
from learnmargin.demo import demo_lesson
from learnmargin.models import GenerateRequest
from learnmargin.storage import Store, atomic_json, new_id, now


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("LEARNMARGIN_API_KEY", "LEARNMARGIN_BASE_URL", "LEARNMARGIN_MODEL", "LEARNMARGIN_PROTOCOL",
                "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(tmp_path)
    with TestClient(app) as value:
        yield value


def upload(client):
    response = client.post("/api/documents", files={"file": ("材料.md", "# 条件概率\n已知B发生时，在B中计算A的比例。".encode(), "text/markdown")})
    assert response.status_code == 201, response.text
    return response.json()


def test_reasoning_capabilities_and_invalid_choices_at_http_boundary(client, monkeypatch):
    from learnmargin.reasoning import public_reasoning_profiles

    assert client.get("/api/settings").json()["reasoning_profiles"] == public_reasoning_profiles()
    document = upload(client)

    def forbidden(*args, **kwargs):
        pytest.fail("An invalid effort must not start a provider or job")

    monkeypatch.setattr("learnmargin.app.Provider", forbidden)
    api = {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3-flash",
           "reasoning_effort": "medium", "api_key": "fake-test-key"}
    for path, body in [("/api/connection-test", api),
                       ("/api/jobs", {"document_ids": [document["id"]], "api": api})]:
        response = client.post(path, json=body)
        assert response.status_code == 400
        assert "思考" in response.json()["detail"]
    assert client.get("/api/jobs").json() == []


def wait_job(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    raise AssertionError("Job did not finish")


async def fake_render(lesson, directory: Path, *, layout="a4"):
    for name in ("lesson.pdf", "lesson.html", "lesson.json", "validation.json"):
        (directory / name).write_text("test artifact", encoding="utf-8")
    return {"page_count": 3}


def test_import_preview_delete_and_path_boundary(client):
    document = upload(client)
    assert document["total_units"] == 1
    detail = client.get(f"/api/documents/{document['id']}/units/1").json()
    assert "条件概率" in detail["text"]
    assert client.get(f"/api/documents/{document['id']}/units/999").status_code == 404
    assert client.get("/api/documents/not-an-id").status_code == 400
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    assert client.get(f"/api/documents/{document['id']}").status_code == 404


def test_import_failure_cleans_temporary_document(client):
    assert client.post("/api/documents", files={"file": ("bad.pdf", b"not pdf")}).status_code == 400
    assert list((client.app.state.store.root / "documents").iterdir()) == []
    assert client.post("/api/documents", files={"file": ("run.exe", b"data")}).status_code == 400


def test_job_source_returns_transcription_only_for_cited_completed_source(client):
    source, other = upload(client), upload(client)
    store = client.app.state.store
    job_id = new_id()
    job = {"id": job_id, "status": "completed", "selection": [{"ref": f"{source['id']}:1"}]}
    store.save_job(job)
    atomic_json(store.directory("jobs", job_id) / "transcription.json", {"units": [
        {"ref": f"{source['id']}:1", "text": "识读公式 $x^2$", "uncertainties": ["指数需对照原图"]}]})
    base = f"/api/jobs/{job_id}/sources"
    response = client.get(f"{base}/{source['id']}/1")
    assert response.status_code == 200
    assert response.json()["transcription"]["uncertainties"] == ["指数需对照原图"]
    assert "条件概率" in response.json()["text"]
    assert client.get(f"{base}/{other['id']}/1").status_code == 404
    assert client.get(f"{base}/{source['id']}/99").status_code == 404
    job["status"] = "running"
    store.save_job(job)
    assert client.get(f"{base}/{source['id']}/1").status_code == 404


def test_handwriting_rejects_disabled_vision_before_job(client):
    document = upload(client)
    response = client.post("/api/jobs", json={"document_ids": [document["id"]],
        "reading_mode": "handwritten", "api": {"vision": False, "api_key": "fake"}})
    assert response.status_code == 400 and "多模态" in response.json()["detail"]
    assert not client.get("/api/jobs").json()


def test_cross_origin_post_and_bad_host_are_rejected(client):
    response = client.post("/api/demo", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert client.get("/api/settings", headers={"Host": "attacker.example"}).status_code == 400


def test_validation_never_reflects_secret(client):
    response = client.post("/api/jobs", json={"document_ids": [], "api": {"api_key": "not-a-real-key-123", "unknown": "data"}})
    assert response.status_code == 422
    assert "not-a-real-key-123" not in response.text
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("legacy_count", [4, 12, "retired-control"])
def test_legacy_section_count_is_ignored_by_api_and_never_persisted(client, monkeypatch, legacy_count):
    captured = []

    async def generated(request, *_):
        captured.append(request.model_dump(mode="json"))
        return demo_lesson()

    monkeypatch.setattr("learnmargin.app.generate_lesson", generated)
    monkeypatch.setattr(rendering, "render_lesson", fake_render)
    document = upload(client)
    payload = {"document_ids": [document["id"]], "section_count": legacy_count,
               "api": {"api_key": "in-memory-legacy-test-key"}}
    submitted = client.post("/api/jobs", json=payload)
    assert submitted.status_code == 202, submitted.text
    result = wait_job(client, submitted.json()["id"])
    assert result["status"] == "completed", result
    assert len(captured) == 1 and "section_count" not in captured[0]
    assert "section_count" not in client.app.state.store.job(result["id"])["request"]
    schema = client.get("/openapi.json").json()["components"]["schemas"]["GenerateRequest"]
    assert "section_count" not in schema["properties"]
    assert schema["additionalProperties"] is False
    payload["unknown_option"] = "still-invalid"
    assert client.post("/api/jobs", json=payload).status_code == 422
    assert len(client.get("/api/jobs").json()) == 1


def test_missing_key_and_invalid_scope_fail_before_job(client):
    document = upload(client)
    request = GenerateRequest(document_ids=[document["id"]]).model_dump(mode="json")
    request["api"]["api_key"] = ""
    assert client.post("/api/jobs", json=request).status_code == 400
    request["scope"] = {"mode": "pages", "ranges": {document["id"]: "2-9"}}
    assert client.post("/api/jobs", json=request).status_code == 400
    assert client.get("/api/jobs").json() == []


def test_page_job_accepts_unranged_reference_material(client, monkeypatch):
    async def waiting(*_):
        await asyncio.sleep(60)
    monkeypatch.setattr("learnmargin.app.generate_lesson", waiting)
    primary, reference = upload(client), upload(client)
    request = GenerateRequest(document_ids=[primary["id"], reference["id"]]).model_dump(mode="json")
    request["api"]["api_key"] = "fake-page-reference-key"
    request["scope"] = {"mode": "pages", "ranges": {primary["id"]: "1"}}
    response = client.post("/api/jobs", json=request)
    assert response.status_code == 202, response.text
    item_id = response.json()["id"]
    stored = client.app.state.store.job(item_id)
    assert stored["request"]["document_ids"] == [primary["id"], reference["id"]]
    assert stored["request"]["scope"]["ranges"] == {primary["id"]: "1"}
    assert client.delete(f"/api/documents/{reference['id']}").status_code == 409
    assert client.post(f"/api/jobs/{item_id}/cancel").json()["status"] == "cancelled"


@pytest.mark.parametrize("ranges_case", ["none", "blank", "unknown"])
def test_page_job_rejects_missing_or_unselected_primary_ranges_before_queue(client, ranges_case):
    primary, reference = upload(client), upload(client)
    request = GenerateRequest(document_ids=[primary["id"], reference["id"]]).model_dump(mode="json")
    request["api"]["api_key"] = "fake-page-reference-key"
    ranges = {} if ranges_case == "none" else {primary["id"]: " "} if ranges_case == "blank" else {"d" * 32: "1"}
    request["scope"] = {"mode": "pages", "ranges": ranges}
    response = client.post("/api/jobs", json=request)
    assert response.status_code == 400, response.text
    assert client.get("/api/jobs").json() == []


def test_demo_uses_actual_job_state_without_provider(client, monkeypatch):
    def no_provider(*_):
        raise AssertionError("A demonstration must not call a provider")
    monkeypatch.setattr("learnmargin.app.Provider", no_provider)
    monkeypatch.setattr(rendering, "render_lesson", fake_render)
    response = client.post("/api/demo")
    assert response.status_code == 202
    result = wait_job(client, response.json()["id"])
    assert result["status"] == "completed" and result["demo"] is True
    assert result["page_count"] == 3
    assert client.get(result["artifacts"]["pdf"]).status_code == 200
    assert client.get(result["artifacts"]["source_zip"]).headers["content-type"] == "application/zip"
    assert client.get(f"/api/jobs/{result['id']}/artifacts/job.json").status_code == 404


def test_source_archive_includes_section_evidence_review_without_new_download_endpoint(client, monkeypatch):
    review = {"status": "completed", "original_refs": {"s1": ["source:1"]},
              "additions": [{"section_id": "s1", "source_refs": ["source:2"],
                             "reason": "后页补足目标结论的条件。"}],
              "revised_refs": {"s1": ["source:1", "source:2"]}}

    async def generated(_request, _documents, _store, output, _provider, _progress):
        atomic_json(output / "section-source-review.json", review)
        return demo_lesson()

    monkeypatch.setattr("learnmargin.app.generate_lesson", generated)
    monkeypatch.setattr(rendering, "render_lesson", fake_render)
    document = upload(client)
    payload = GenerateRequest(document_ids=[document["id"]]).model_dump(mode="json")
    payload["api"]["api_key"] = "in-memory-archive-test-key"
    submitted = client.post("/api/jobs", json=payload)
    assert submitted.status_code == 202
    result = wait_job(client, submitted.json()["id"])
    assert result["status"] == "completed", result
    response = client.get(result["artifacts"]["source_zip"])
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("section-source-review.json")) == review
        assert "job.json" not in archive.namelist()
    assert client.get(f"/api/jobs/{result['id']}/artifacts/section-source-review.json").status_code == 404


def test_secret_not_persisted_and_cancel_blocks_document_deletion(client, monkeypatch):
    async def waiting(*_):
        await asyncio.sleep(60)
    monkeypatch.setattr("learnmargin.app.generate_lesson", waiting)
    document = upload(client)
    request = GenerateRequest(document_ids=[document["id"]]).model_dump(mode="json")
    request["api"]["api_key"] = "only-in-memory-example"
    response = client.post("/api/jobs", json=request)
    assert response.status_code == 202
    item_id = response.json()["id"]
    assert client.delete(f"/api/documents/{document['id']}").status_code == 409
    assert "only-in-memory-example" not in json.dumps(client.get("/api/jobs").json())
    persisted = (client.app.state.store.directory("jobs", item_id) / "job.json").read_text(encoding="utf-8")
    assert "only-in-memory-example" not in persisted and "api_key" not in persisted
    assert client.post(f"/api/jobs/{item_id}/cancel").json()["status"] == "cancelled"
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200


def test_failed_generation_has_no_downloads(client, monkeypatch):
    async def failed(*_):
        raise ValueError("测试中的明确失败")
    monkeypatch.setattr("learnmargin.app.generate_lesson", failed)
    document = upload(client)
    request = GenerateRequest(document_ids=[document["id"]]).model_dump(mode="json")
    request["api"]["api_key"] = "example-key"
    item_id = client.post("/api/jobs", json=request).json()["id"]
    job = wait_job(client, item_id)
    assert job["status"] == "failed" and job["error"] == "准备材料：测试中的明确失败"
    assert job["failed_stage"] == "准备材料"
    assert job["artifacts"] == {}


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    store = Store(tmp_path)
    item_id = new_id()
    store.save_job({"id": item_id, "status": "running", "created_at": now()})
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/jobs/{item_id}").json()["status"] == "failed"


@pytest.mark.integration
def test_demo_http_to_real_a4_pdf(client):
    from pypdf import PdfReader
    item_id = client.post("/api/demo").json()["id"]
    for _ in range(200):
        result = client.get(f"/api/jobs/{item_id}").json()
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(.05)
    assert result["status"] == "completed", result
    reader = PdfReader(client.app.state.store.directory("jobs", item_id) / "lesson.pdf")
    assert len(reader.pages) == result["page_count"]
    assert abs(float(reader.pages[0].mediabox.width) - 595.28) < 1
    assert "条件概率" in reader.pages[0].extract_text()
    assert any("参考答案" in page.extract_text() for page in reader.pages)
