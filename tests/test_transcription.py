from __future__ import annotations

import json

import pytest

from learnmargin.models import Document, SourceUnit
from learnmargin.pipeline import make_units
from learnmargin.storage import Store
from learnmargin.transcription import transcribe_sources

DOC_ID = "c" * 32


class Reader:
    def __init__(self):
        self.calls = []

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
