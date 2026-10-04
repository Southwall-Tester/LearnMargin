from __future__ import annotations

import json
import re
from pathlib import Path
from unicodedata import normalize

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError
from pypdf import PdfReader

from learnmargin.demo import demo_lesson
from learnmargin.localization import LessonText, chinese_lesson_text
from learnmargin.models import (
    Concept,
    Lesson,
    LessonPlan,
    LessonSection,
    Overview,
    Pause,
    Practice,
    SourceCitation,
    StudyPrompt,
)
from learnmargin.rendering import build_html, render_lesson


def english_lesson_text() -> LessonText:
    return LessonText(
        language_tag="en", document="Study notes", overview="Overview", global_view="The whole picture",
        concept_relations="Connections", learning_path="Learning path", study_rhythm="Study rhythm",
        rhythm_note="Try 25 minutes of focus and a 5-minute break. Note your stopping point if the timer rings "
                    "or you feel tired; adjust the intervals to suit you.",
        prerequisites="Prerequisites", contents="Contents", answers="Reference answers",
        review_sources="Review and sources", section="Section", material="Material", view_source="View source",
        source_comparison="Source comparison", worked_example="Worked example", practice="Independent practice",
        hint="Hint", try_then_answer="After trying, check the answer", view_answer="View reference answer",
        pause="Break", minutes="minutes", after_pause="On returning",
        answer_note="Try independently, then find the first difference. Revisit the explanation if needed.",
        return_question="Return to question", sidebar="Sidebar", return_prompt="Return to this prompt",
        review_step="Review step", method_basis="Learning methods",
        method_note="These methods draw on chapter summaries of A Mind for Numbers. The examples and prompts "
                    "are adapted to the material; adjust the pace after trying the notes.",
        method_chapters="Reference chapters", source_primary="Primary material", source_reference="Reference",
        source_topic="Topic-related material", material_notes="Material and scope notes",
        return_overview="Return to overview", image="Image",
        scope_all="All imported content", scope_primary="Primary material units", scope_reference="Reference units",
        scope_topic="Topic", scope_units="Material units",
        scope_location_note="Locations use file pages, slides or section numbers, not printed page numbers.",
        pause_when="After this section, take a 5-minute break if you have focused for about 25 minutes. "
                   "If the timer rings first, note where you stopped.",
        pause_activity="Set the notes aside, stand up, and get some water.",
        pause_resume="Recall the main relationship in one sentence before continuing.",
    )


def english_lesson() -> Lesson:
    text = english_lesson_text()
    return Lesson(
        title="Conditional probability", subtitle="Choose the reference group first", language="English", text=text,
        scope_note="Primary material units: 1; Reference units: 0.",
        overview=Overview(
            summary="Conditional probability changes the group within which a probability is calculated.",
            concepts=[Concept(name="Conditional probability", explanation="Count only outcomes in the given event.",
                              connections="The intersection is the numerator, and the given event is the denominator.")],
            learning_path=["Identify the given event.", "Choose the denominator, then calculate."],
            prerequisites=["Fractions and intersections"],
        ),
        sections=[LessonSection(
            id="s1", title="Choosing the denominator", source_refs=["fixture:2"],
            explanation="Once event B is given, restrict the reference group to B. The denominator must be positive."
                        "\n\n$$P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)},\\quad P(B)>0.$$",
            worked_example="Of 30 students, 10 wear glasses, and 6 of those are club members. Given glasses, "
                           "there are 10 possible students, so the conditional probability is 6/10.",
            practice=[Practice(id="s1-q1", prompt="Of 12 striped cards, 3 are red. Given a striped card, "
                               "what is the probability it is red?", hint="Use the striped cards as your group.",
                               answer="The probability is 3/12 = 1/4, because the given event contains 12 cards.")],
            study_prompts=[StudyPrompt(id="s1-a1", kind="question", when="After the example",
                                      task="Cover the solution and explain why the denominator is 10.",
                                      check="Compare the given event with your chosen group.",
                                      answer="Glasses are given, so only the 10 students who wear glasses remain.")],
            pause=Pause(when=text.pause_when, activity=text.pause_activity, resume=text.pause_resume),
        )],
        review_plan=["Tomorrow, choose the denominator from the question without looking at the answer."],
        method_chapters=[4, 7], sources=[SourceCitation(ref="fixture:2", document="原始讲义.pdf",
                                                       label="第 2 页（印刷第 1 页）")],
        warnings=["原始材料识读提示"],
    )


def test_old_lessons_keep_chinese_defaults_but_new_plans_require_complete_text():
    old = demo_lesson().model_dump(exclude={"text", "language"})
    lesson = Lesson.model_validate(old)
    assert lesson.language == "简体中文"
    assert lesson.text == chinese_lesson_text()
    with pytest.raises(ValidationError):
        LessonText.model_validate({})
    schema = LessonPlan.model_json_schema()
    assert "text" in schema["required"]
    assert set(schema["$defs"]["LessonText"]["required"]) == set(LessonText.model_fields)
    with pytest.raises(ValidationError):
        LessonText.model_validate({**chinese_lesson_text().model_dump(), "pause_resume": " "})


def test_localized_labels_are_escaped_and_original_source_locations_are_preserved():
    lesson = english_lesson()
    lesson.text.answers = '<script>UNTRUSTED_LABEL</script>'
    lesson.sections[0].explanation += "\n\n![example](https://invalid.example/image.png)"
    soup = BeautifulSoup(build_html(lesson), "html.parser")
    assert soup.html["lang"] == "en"
    assert len(soup.find_all("script")) == 2
    assert soup.select_one(".image-description").get_text() == "[Image: example]"
    assert "原始讲义.pdf" in soup.get_text() and "第 2 页（印刷第 1 页）" in soup.get_text()
    assert "原始材料识读提示" in soup.get_text()
    assert soup.select_one('#answers h2').get_text() == '<script>UNTRUSTED_LABEL</script>'


@pytest.mark.integration
@pytest.mark.parametrize("language", ["zh-CN", "en"])
async def test_actual_pdf_localizes_fixed_text_and_preserves_navigation(tmp_path: Path, language):
    lesson = demo_lesson() if language == "zh-CN" else english_lesson()
    report = await render_lesson(lesson, tmp_path)
    reader = PdfReader(tmp_path / "lesson.pdf")
    extracted = " ".join(page.extract_text() for page in reader.pages)
    extracted = normalize("NFKC", extracted).translate({0x2ED3: 0x957F, 0x2EDA: 0x9875})
    compact = "".join(extracted.split())
    expected = [lesson.text.global_view, lesson.text.answers, lesson.text.return_prompt, lesson.text.pause,
                lesson.text.minutes, lesson.text.method_basis, lesson.text.source_primary]
    assert all("".join(normalize("NFKC", label).split()) in compact for label in expected)
    assert report["content_preserved"] and report["internal_links"] >= 6
    assert report["sidebar_navigation"] and report["answer_section_page"] > 0
    assert not report["overflow"] and not report["browser_errors"] and not report["blocked_requests"]
    saved = json.loads((tmp_path / "lesson.json").read_text(encoding="utf-8"))
    assert saved["language"] == lesson.language and saved["text"]["language_tag"] == language
    if language == "en":
        for original in (lesson.sources[0].document, lesson.sources[0].label, lesson.warnings[0]):
            canonical = "".join(normalize("NFKC", original).split())
            assert canonical in compact
            compact = compact.replace(canonical, "")
        assert not re.search(r"[\u3400-\u9fff]", compact)
        assert "5minutes" in compact and "25minutes" in compact
