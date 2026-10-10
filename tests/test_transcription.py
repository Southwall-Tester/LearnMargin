from __future__ import annotations

import asyncio
import json

import pytest

from learnmargin.models import APIConfig, Document, SourceUnit
from learnmargin.pipeline import make_units
from learnmargin.provider import ProviderError
from learnmargin.storage import Store
from learnmargin.transcription import transcribe_sources

DOC_ID = "c" * 32


class Reader:
    def __init__(self):
        self.calls = []
        self.config = APIConfig(model="test-reader", api_key="secret-never-saved")

    async def generate(self, schema, system, user, images):
        self.calls.append((system, user, images))
        return schema(text="中文手写：$x_1=\\frac{a}{b}$。第二行[待核对：可能是y或v]。",
                      uncertainties=["第二行首字母可能是y或v"])


def make_source(tmp_path, text=""):
    store = Store(tmp_path / "data")
    folder = store.directory("documents", DOC_ID)
    folder.mkdir()
    (folder / "page.png").write_bytes(b"local image fixture")
    doc = Document(id=DOC_ID, name="手写讲义.pdf", kind="pdf", unit_label="页", units=[
        SourceUnit(index=1, label="PDF 第1页", text=text, image_paths=["page.png"])])
    return store, doc


async def test_scan_transcription_retains_images_math_uncertainties_and_original(tmp_path):
    store, doc = make_source(tmp_path)
    reader = Reader()
    units, warnings = await transcribe_sources(make_units([doc]), store, tmp_path / "job", reader,
        vision=True, reading_mode="auto", progress=lambda *_: None)
    unit = units[f"{DOC_ID}:1"][1]
    assert "\\frac{a}{b}" in unit.text and "[待核对" in unit.text
    assert unit.image_paths == ["page.png"]
    assert doc.units[0].text == ""
    assert reader.calls[0][2][0].read_bytes() == b"local image fixture"
    assert "不要自行解题" in reader.calls[0][1]
    assert any("1 处" in warning for warning in warnings)
    record = json.loads((tmp_path / "job" / "transcription.json").read_text(encoding="utf-8"))["units"][0]
    assert record["ref"] == f"{DOC_ID}:1" and record["uncertainties"]


async def test_handwriting_mode_reads_image_despite_printed_footer_text(tmp_path):
    store, doc = make_source(tmp_path, "足够长的印刷页脚不能代表手写公式已经提取。" * 4)
    reader = Reader()
    await transcribe_sources(make_units([doc]), store, tmp_path / "job", reader,
        vision=True, reading_mode="handwritten", progress=lambda *_: None)
    assert len(reader.calls) == 1


async def test_auto_text_material_does_not_pay_for_extra_transcription(tmp_path):
    store, doc = make_source(tmp_path, "可提取的正文讲解完整而清晰。" * 8)
    reader = Reader()
    units, warnings = await transcribe_sources(make_units([doc]), store, tmp_path / "job", reader,
        vision=True, reading_mode="auto", progress=lambda *_: None)
    assert not reader.calls and not warnings
    assert units[f"{DOC_ID}:1"][1].text == doc.units[0].text


async def test_scans_require_a_multimodal_model_before_transcription(tmp_path):
    store, doc = make_source(tmp_path)
    reader = Reader()
    with pytest.raises(ValueError, match="多模态"):
        await transcribe_sources(make_units([doc]), store, tmp_path / "job", reader,
            vision=False, reading_mode="auto", progress=lambda *_: None)
    assert not reader.calls


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_page_preserves_prefix_and_new_job_reuses_it(tmp_path, cancelled):
    store, doc = make_source(tmp_path)
    doc.units.append(doc.units[0].model_copy(update={"index": 2, "label": "PDF 第2页"}))

    class FailingReader(Reader):
        async def generate(self, *args):
            if len(self.calls) == 1:
                raise asyncio.CancelledError() if cancelled else ProviderError("模型响应超时")
            return await super().generate(*args)

    first = tmp_path / "first"
    with pytest.raises(asyncio.CancelledError if cancelled else ProviderError) as failure:
        await transcribe_sources(make_units([doc]), store, first, FailingReader(),
            vision=True, reading_mode="handwritten", progress=lambda *_: None)
    if not cancelled:
        assert "PDF 第2页" in str(failure.value) and "已等待" in str(failure.value)
    records = json.loads((first / "transcription.json").read_text(encoding="utf-8"))["units"]
    assert [r["ref"] for r in records] == [f"{DOC_ID}:1"]
    state = json.loads((first / "transcription-status.json").read_text(encoding="utf-8"))
    assert state["status"] == ("cancelled" if cancelled else "failed")
    reader = Reader()
    reader.config.timeout_seconds = 600
    reader.config.api_key = APIConfig(api_key="changed-key").api_key
    output = tmp_path / "second"
    units, _ = await transcribe_sources(make_units([doc]), store, output, reader,
        vision=True, reading_mode="handwritten", progress=lambda *_: None)
    assert len(reader.calls) == 1
    assert all(unit.transcription_complete and unit.image_paths for _, unit in units.values())
    saved = json.loads((output / "transcription.json").read_text(encoding="utf-8"))["units"]
    assert [r["reused"] for r in saved] == [True, False]
    assert all(not unit.transcription_complete and not unit.text for unit in doc.units)
    for cache in store.directory("documents", DOC_ID).glob("transcription-*.json"):
        text = cache.read_text(encoding="utf-8")
        assert "secret-never-saved" not in text and "changed-key" not in text


@pytest.mark.parametrize("change", ["image", "text", "model", "endpoint", "protocol", "effort", "mode", "rules", "corrupt"])
async def test_transcription_cache_is_invalidated_by_relevant_changes(tmp_path, monkeypatch, change):
    store, doc = make_source(tmp_path)
    reader = Reader()
    await transcribe_sources(make_units([doc]), store, tmp_path / "first", reader,
        vision=True, reading_mode="auto", progress=lambda *_: None)
    mode = "auto"
    if change == "image":
        (store.directory("documents", DOC_ID) / "page.png").write_bytes(b"different pixels")
    elif change == "text":
        doc.units[0].text = "different"
    elif change == "model":
        reader.config.model = "other-model"
    elif change == "endpoint":
        reader.config.base_url = "https://other.example/v1"
    elif change == "protocol":
        reader.config.protocol = "responses"
    elif change == "effort":
        reader.config.reasoning_effort = "low"
    elif change == "mode":
        mode = "handwritten"
    elif change == "rules":
        monkeypatch.setattr("learnmargin.transcription.TRANSCRIPTION_VERSION", 2)
    else:
        store.transcription_cache(DOC_ID, 1).write_text("broken json")
    await transcribe_sources(make_units([doc]), store, tmp_path / "second", reader,
        vision=True, reading_mode=mode, progress=lambda *_: None)
    assert len(reader.calls) == 2


async def test_reference_failure_does_not_overwrite_primary_checkpoint(tmp_path):
    store, doc = make_source(tmp_path)
    output = tmp_path / "job"
    await transcribe_sources(make_units([doc]), store, output, Reader(),
        vision=True, reading_mode="auto", progress=lambda *_: None)
    doc.units[0].index = 2

    class FailingReader(Reader):
        async def generate(self, *args):
            raise ProviderError("模型响应超时")

    with pytest.raises(ProviderError):
        await transcribe_sources(make_units([doc]), store, output, FailingReader(),
            vision=True, reading_mode="auto", progress=lambda *_: None, append=True)
    assert len(json.loads((output / "transcription.json").read_text(encoding="utf-8"))["units"]) == 1
    store.delete_document(DOC_ID)
    assert not store.transcription_cache(DOC_ID, 1).exists()
