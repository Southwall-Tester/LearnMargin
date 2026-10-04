"""The optional standalone skill renderer uses its own document dependencies."""
import argparse
import asyncio
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="Standalone skill renderer requires optional PyMuPDF")
pytest.importorskip("playwright")
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "learnmargin" / "scripts" / "add_sidebars.py"
spec = importlib.util.spec_from_file_location("skill_sidebars", SCRIPT)
sidebars = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidebars)


def card(card_id, **kwargs):
    return {"id": card_id, "anchor_y_pt": 210, "label": "Read then try", "title": "Sum",
            "body": ["Find the sum of 2 and 3."], **kwargs}


@pytest.mark.parametrize("value,expected", [(0, 0), ("0.25", 0.25), (1.25, 1.25), ("4", 4)])
def test_navigation_zoom_accepts_reading_scale_or_preserving_current_zoom(value, expected):
    assert sidebars.parse_navigation_zoom(value) == expected


@pytest.mark.parametrize("value", [-1, 0.1, 4.1, "nan", "inf", "-inf", "text", None, True])
def test_navigation_zoom_rejects_invalid_values_before_rendering(value):
    with pytest.raises(argparse.ArgumentTypeError, match="navigation zoom must be 0"):
        asyncio.run(sidebars.build(Namespace(navigation_zoom=value)))


@pytest.mark.parametrize("value", [None, "", "  ", [], [" "]])
def test_declared_question_requires_an_answer(value):
    with pytest.raises(ValueError, match="question requires a nonempty answer"):
        sidebars.validate_cards({"pages": [{"cards": [card("1A", kind="question", answer=value)]}]})


def test_legacy_actions_do_not_use_vocabulary_to_invent_answers():
    cards = [card("1A", kind="action", body=["Set a timer and cover the solution."]),
             card("1B", body=["Legacy operation input remains readable."])]
    assert sidebars.validate_cards({"pages": [{"cards": cards}]}) == []
    with pytest.raises(ValueError, match="unique"):
        sidebars.validate_cards({"pages": [{"cards": [cards[0], cards[0]]}]})
    with pytest.raises(ValueError, match="replace requires_answer"):
        sidebars.validate_cards({"pages": [{"cards": [card("1C", requires_answer=True)]}]})
    with pytest.raises(ValueError, match="must use kind=question"):
        sidebars.validate_cards({"pages": [{"cards": [card("1C", kind="action", answer="5")]}]})


def write_fixture(directory, *, questions):
    directory.mkdir(parents=True, exist_ok=True)
    source = fitz.open()
    for index in range(2):
        page = source.new_page(width=595.276, height=841.89)
        page.insert_text((40, 70), f"Source page {index + 1}: 2 + 3 = 5.")
    source[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(40, 60, 210, 76),
                           "page": 1, "to": fitz.Point(40, 70)})
    source.save(directory / "body.pdf")
    source.close()
    width = 595.276 + 78 * 72 / 25.4
    overview = f"""<!doctype html><meta charset="utf-8"><style>
@page {{size:{width}pt 841.89pt;margin:0}} body {{margin:0}}
.sheet {{height:841.89pt;padding:45pt;box-sizing:border-box}}
.meta {{margin-top:20pt}} aside .card {{margin-top:20pt}}
.aside-foot {{margin-top:50pt}}</style>
<section class="sheet"><h1>Addition overview</h1><p>Combine quantities to find their sum.</p>
<p class="meta">Two short examples</p><aside><div class="card">Read, then try.</div>
<p class="aside-foot">Check only after trying.</p></aside></section>"""
    (directory / "overview.html").write_text(overview, encoding="utf-8")
    pages = []
    for index in range(1, 3):
        cards = [card(f"{index}B", anchor_y_pt=480, kind="action", title="Pause",
                      body=["若本轮已专注约25分钟，休息5分钟。"], check="回来继续下一节。")]
        if questions:
            cards.insert(0, card(f"{index}A", kind="question", response_lines=1,
                                 answer=[f"ANSWER_{index}: 5. 两组数量相加，共五个。"],
                                 check=f"CHECK_{index}: count all five."))
        else:
            del cards[0]["kind"]
        pages.append({"page": index, "title": f"Source {index}", "goal": "Find a sum.",
                      "cards": cards, "footer": "Continue with the next example."})
    notes = {"title": "Standalone answers test", "version": "test", "method_source": "Authored fixture",
             "pages": pages}
    (directory / "notes.json").write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    return Namespace(source=directory / "body.pdf", output=directory / "guided.pdf",
                     notes=directory / "notes.json", overview=directory / "overview.html",
                     report=directory / "report.json", preview_dir=directory / "previews")


@pytest.mark.integration
@pytest.mark.parametrize("questions,answer_paragraphs,navigation_zoom", [(True, 1, None), (True, 20, 0), (True, 1, 1.3333333333), (False, 0, None)])
def test_standalone_pdf_answers_and_exact_return_targets(tmp_path, questions, answer_paragraphs, navigation_zoom):
    args = write_fixture(tmp_path, questions=questions)
    if navigation_zoom is not None:
        args.navigation_zoom = navigation_zoom
    if answer_paragraphs > 1:
        notes = json.loads(args.notes.read_text(encoding="utf-8"))
        for page in notes["pages"]:
            page["cards"][0]["answer"] *= answer_paragraphs
        args.notes.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    asyncio.run(sidebars.build(args))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    expected_zoom = 1.25 if navigation_zoom is None else navigation_zoom
    assert report["navigation_zoom"] == expected_zoom
    assert len(report["source_preservation"]) == 2
    assert len(report["internal_links_preserved"]) == 1
    with fitz.open(args.output) as pdf:
        before = "".join(page.get_text() for page in list(pdf)[:3])
        if questions:
            assert report["answer_pages"] == (2 if answer_paragraphs > 1 else 1)
            assert len(report["sidebar_navigation"]) == 2
            after = "".join(page.get_text() for page in list(pdf)[3:])
            for index in (1, 2):
                assert f"ANSWER_{index}" not in before
                assert f"CHECK_{index}" not in before
                assert f"ANSWER_{index}: 5." in after
                assert f"CHECK_{index}" in after
            for pair in report["sidebar_navigation"]:
                for direction in ("forward", "return"):
                    expected = pair[direction]
                    assert expected["zoom"] == expected_zoom
                    links = pdf[expected["source_pdf_page"] - 1].get_links()
                    actual = next(link for link in links if link.get("page") == expected["target_pdf_page"] - 1
                                  and max(abs(a - b) for a, b in zip(link["from"], expected["from"])) < 0.1)
                    # Read the serialized destination: get_links()['zoom'] may
                    # incorrectly return 0 in some MuPDF versions.
                    kind, destination = pdf.xref_get_key(actual["xref"], "A/D")
                    assert kind == "array" and "/XYZ" in destination
                    assert float(destination.rstrip("] ").split()[-1]) == pytest.approx(expected_zoom)
                returned = pair["return"]
                assert returned["target_pdf_page"] == pair["forward"]["source_pdf_page"]
                assert returned["to"][0] > 595  # Original sidebar, rather than the page/section start.
                assert returned["to"][1] >= 210
                assert "查看参考答案" in pdf[pair["forward"]["source_pdf_page"] - 1].get_text()
                assert "返回这条提示" in pdf[returned["source_pdf_page"] - 1].get_text()
        else:
            assert report["answer_pages"] == 0
            assert report["sidebar_navigation"] == []
            assert report["legacy_untyped_cards"] == ["1B", "2B"]
            assert len(pdf) == 3
            assert "查看参考答案" not in before
