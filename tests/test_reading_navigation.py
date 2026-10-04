from __future__ import annotations

import shutil
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject

from learnmargin import rendering
from learnmargin.demo import demo_lesson
from learnmargin.rendering import NAVIGATION_ZOOM, _set_navigation_zoom, render_lesson


def test_only_owned_named_destinations_get_reading_zoom(tmp_path: Path):
    path = tmp_path / "named-tree.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    for name in ("prompt-1-1", "untouched"):
        writer.add_named_destination_array(TextStringObject(name), ArrayObject([
            page.indirect_reference, NameObject("/XYZ"), FloatObject(400), FloatObject(600), FloatObject(0),
        ]))
    writer.write(path)
    writer.close()
    _set_navigation_zoom(path, {"prompt-1-1": {"page": 1, "left": 400, "top": 600}})
    reader = PdfReader(path)
    target = reader.named_destinations["prompt-1-1"]
    assert float(target.zoom) == NAVIGATION_ZOOM
    assert float(target.left) == 400 and float(target.top) == 600
    assert reader.get_destination_page_number(target) == 0
    assert float(reader.named_destinations["untouched"].zoom) == 0


@pytest.mark.integration
async def test_exported_pdf_keeps_pages_text_pixels_and_exact_bidirectional_positions(tmp_path, monkeypatch):
    original = rendering._set_navigation_zoom
    before_path = tmp_path / "before-navigation.pdf"

    def capture_original(path, anchors):
        shutil.copyfile(path, before_path)
        original(path, anchors)

    monkeypatch.setattr(rendering, "_set_navigation_zoom", capture_original)
    report = await render_lesson(demo_lesson(), tmp_path)
    before, after = PdfReader(before_path), PdfReader(tmp_path / "lesson.pdf")
    assert len(before.pages) == len(after.pages) == report["page_count"]
    assert report["navigation_zoom"] == NAVIGATION_ZOOM
    assert set(before.named_destinations) == set(after.named_destinations)
    assert all(float(destination[4]) == NAVIGATION_ZOOM
               for destination in after.trailer["/Root"]["/Dests"].values())
    for name, destination in after.named_destinations.items():
        previous = before.named_destinations[name]
        assert float(destination.zoom) == NAVIGATION_ZOOM
        assert float(previous.zoom or 0) == 0
        assert (destination.left, destination.top) == (previous.left, previous.top)
        assert after.get_destination_page_number(destination) == before.get_destination_page_number(previous)
    for first, second in zip(before.pages, after.pages):
        assert first.mediabox == second.mediabox
        assert first.extract_text() == second.extract_text()
        assert first.get_contents().get_data() == second.get_contents().get_data()
        assert len(first.get("/Annots", [])) == len(second.get("/Annots", []))
    assert "/StructTreeRoot" in after.trailer["/Root"]
    assert before.trailer["/Root"]["/Lang"] == after.trailer["/Root"]["/Lang"]
    for pair in report["sidebar_navigation"]:
        assert pair["prompt"]["zoom"] == pair["answer"]["zoom"] == NAVIGATION_ZOOM
        assert pair["prompt"]["page"] in pair["answer"]["linked_from_pages"]
        assert pair["answer"]["page"] in pair["prompt"]["linked_from_pages"]
        for target in pair.values():
            assert abs(target["left_pt"] - target["expected_left_pt"]) <= 2
            assert abs(target["top_pt"] - target["expected_top_pt"]) <= 2
    first_pdf, second_pdf = pdfium.PdfDocument(before_path), pdfium.PdfDocument(tmp_path / "lesson.pdf")
    try:
        for index in range(len(first_pdf)):
            first_page, second_page = first_pdf[index], second_pdf[index]
            try:
                assert first_page.render(scale=.5).to_pil().tobytes() == second_page.render(scale=.5).to_pil().tobytes()
            finally:
                first_page.close()
                second_page.close()
    finally:
        first_pdf.close()
        second_pdf.close()


@pytest.mark.integration
@pytest.mark.parametrize("embedded", [False, True])
async def test_html_answer_roundtrip_highlights_exact_target_without_affecting_print(tmp_path, embedded):
    await render_lesson(demo_lesson(), tmp_path)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1050, "height": 780})
            await page.goto((tmp_path / "lesson.html").as_uri())
            await page.wait_for_function("window.learnmarginReport !== undefined")
            if embedded:
                await page.evaluate("document.documentElement.classList.add('embedded-reader')")
            baseline = await page.locator("#prompt-1-1").evaluate("el => ({background: getComputedStyle(el).backgroundColor})")
            await page.locator('#prompt-1-1 a[href="#prompt-answer-1-1"]').click()
            assert await page.evaluate("location.hash") == "#prompt-answer-1-1"
            assert await page.locator(":target").get_attribute("id") == "prompt-answer-1-1"
            assert await page.locator(":target").evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
            await page.locator('#prompt-answer-1-1 a[href="#prompt-1-1"]').click()
            assert await page.evaluate("location.hash") == "#prompt-1-1"
            target = page.locator("#prompt-1-1")
            screen = await target.evaluate("el => ({background: getComputedStyle(el).backgroundColor, "
                                           "margin: getComputedStyle(el).scrollMarginTop, top: el.getBoundingClientRect().top})")
            assert screen["margin"] == "24px" and abs(screen["top"] - 24) < 2
            assert screen["background"] != baseline["background"]
            await page.emulate_media(media="print")
            printed = await target.evaluate("el => ({outline: getComputedStyle(el).outlineStyle, "
                                            "background: getComputedStyle(el).backgroundColor})")
            assert printed["outline"] == "none" and printed["background"] == baseline["background"]
        finally:
            await browser.close()
