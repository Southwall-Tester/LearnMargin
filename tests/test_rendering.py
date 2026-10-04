from __future__ import annotations

from pathlib import Path
from unicodedata import normalize

import pytest
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from pypdf import PdfReader

from learnmargin.models import (
    Concept,
    Lesson,
    LessonSection,
    Overview,
    Pause,
    Practice,
    SourceCitation,
    SourceNote,
    StudyLoad,
    StudyPrompt,
)
from learnmargin.rendering import RenderError, build_html, markdown, render_lesson


def sidebar_pdf_text(reader: PdfReader, left: float) -> str:
    """Read the actual rail across pages without interleaving main text/footers."""
    chunks = []
    for page in reader.pages:
        def visit(text, cm, tm, font, size):
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            if x >= left - 1 and 45 <= y <= float(page.mediabox.height) - 35:
                chunks.append(text)
        page.extract_text(visitor_text=visit)
    return "".join("".join(chunks).split())


def comparable_pdf_text(value: str) -> str:
    # Noto's duplicate CJK glyphs can use radical code points in ToUnicode.
    # These two fixture characters have Equivalent_Unified_Ideograph mappings
    # but no NFKC decomposition: unicode.org/Public/UCD/latest/ucd/EquivalentUnifiedIdeograph.txt
    return normalize("NFKC", value).translate({0x2ED3: 0x957F, 0x2EDA: 0x9875})


def example_lesson(*, long: bool = False, practice: bool = True) -> Lesson:
    explanation = (
        "条件概率先确定已知条件，再在符合条件的对象中计算比例。"
        "公式中的分母是条件事件的概率，必须大于零。"
        "\n\n$$P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)},\\quad P(B)>0.$$")
    if long:
        explanation += "\n\n" + "这是需要完整保留的长段落。先确定研究范围，再说明每一步的依据。" * 160
    return Lesson(
        title="条件概率与独立性", subtitle="确定分母，再判断关系", scope_note="示例材料第 1 节",
        overview=Overview(summary="条件概率把研究范围缩小到已知事件；独立性比较缩小范围前后的概率。",
                          concepts=[Concept(name="条件概率", explanation="在条件发生的对象中计算概率。",
                                            connections="由交集与条件事件构成；可用于检验独立性。")],
                          learning_path=["理解条件，再读例题", "独立计算并核对分母"], prerequisites=["概率与交集"]),
        sections=[LessonSection(id="one", title="条件限定了分母", source_refs=["D1:1"],
                                explanation=explanation,
                                worked_example="班里 30 人，10 人戴眼镜，其中 6 人加入社团。已知戴眼镜，分母为 10，所求概率是 6/10。",
                                practice=[Practice(id="P1", prompt="换一组人数后，先写分母，再列式。",
                                                   hint="只数条件成立的人。", answer="ANSWER_SENTINEL：分母应来自条件事件。")]
                                if practice else [],
                                study_prompts=[StudyPrompt(id="S1", kind="question", when="读完公式后", task="圈出分母，说明它代表哪一组对象。",
                                                          check="尝试后核对末尾 S1 的判断要点。",
                                                          answer="SIDEBAR_SENTINEL：分母是条件事件 B 的概率。")],
                                pause=Pause(minutes=5, when="若已专注约 25 分钟，休息 5 分钟。",
                                            activity="起身走动。", resume="先回想分母，再继续。"))],
        review_plan=["次日先重算例题，再核对第一处差异。"], method_chapters=[4, 7],
        sources=[SourceCitation(ref="D1:1", document="示例材料", label="第 1 节")],
    )


def test_markdown_disables_html_remote_images_and_active_links():
    rendered = str(markdown('<script>window.BAD=1</script>\n\n![image](https://evil.test/a.png) '
                            '[click](https://evil.test) $\\frac{1}{2}$'))
    soup = BeautifulSoup(rendered, "html.parser")
    assert not soup.find_all(["script", "img", "a"])
    assert soup.select_one(".math")["data-tex"] == r"\frac{1}{2}"
    assert "window.BAD" in soup.get_text()


def test_build_uses_self_contained_fonts_and_escaped_title():
    lesson = example_lesson()
    lesson.title = '</title><script>window.BAD=1</script>'
    html = build_html(lesson)
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.find_all("script")) == 2  # only bundled KaTeX and our paginator
    assert not soup.find_all("script", src=True)
    assert "data:font/woff2;base64," in html
    assert "url(fonts/" not in html
    assert soup.title.get_text().startswith(lesson.title)


@pytest.mark.integration
async def test_render_a4_long_content_navigation_and_answer_isolation(tmp_path: Path):
    lesson = example_lesson(long=True)
    lesson.sections[0].study_prompts[0].check = "CHECK_SPOILER_SENTINEL：核对分母应为条件事件的概率。"
    lesson.sections[0].pause.resume = r"回来先回想 $P(B)>0$，再继续下一节。"
    lesson.sections[0].explanation += '\n\n<script>window.BAD=1</script> ![remote](https://invalid.test/image.png)'
    result = await render_lesson(lesson, tmp_path)
    reader = PdfReader(tmp_path / "lesson.pdf")
    assert result["page_count"] >= 6
    assert len(result["pause_positions"]) == 1  # Long legacy content preserves only its authored pause.
    assert result["math_count"] == 2
    assert result["content_preserved"]
    assert result["internal_links"] >= 5
    assert result["overflow"] == []
    assert result["orphan_heading_pages"] == [] and result["pause_only_pages"] == []
    assert result["blocked_requests"] == [] and result["browser_errors"] == []
    assert abs(float(reader.pages[0].mediabox.width) - 595.28) < 1
    answer_index = result["answer_section_page"] - 1
    assert "ANSWER_SENTINEL" not in "".join(page.extract_text() for page in reader.pages[:answer_index])
    assert "ANSWER_SENTINEL" in "".join(page.extract_text() for page in reader.pages[answer_index:])
    assert "SIDEBAR_SENTINEL" not in "".join(page.extract_text() for page in reader.pages[:answer_index])
    assert "SIDEBAR_SENTINEL" in "".join(page.extract_text() for page in reader.pages[answer_index:])
    assert "CHECK_SPOILER_SENTINEL" not in "".join(page.extract_text() for page in reader.pages[:answer_index])
    assert "CHECK_SPOILER_SENTINEL" in "".join(page.extract_text() for page in reader.pages[answer_index:])
    # Noto CJK may map visually identical glyphs to compatibility radicals in
    # PDF ToUnicode; compare canonical content without dropping characters.
    extracted = comparable_pdf_text("".join(page.extract_text() for page in reader.pages))
    assert "D1:1" not in extracted
    assert "$P(B)>0$" not in extracted
    assert "[1] 示例材料" in extracted
    assert "".join(extracted.split()).count("这是需要完整保留的长段落") == 160
    assert (tmp_path / "lesson.html").is_file() and (tmp_path / "lesson.json").is_file()


@pytest.mark.integration
async def test_invalid_math_fails_instead_of_shipping_raw_tex(tmp_path: Path):
    lesson = example_lesson()
    lesson.sections[0].explanation += r" $$\ThisIsNotACommand{a}$$"
    with pytest.raises(RenderError, match="公式无法排版"):
        await render_lesson(lesson, tmp_path)
    assert not (tmp_path / "lesson.pdf").exists()


@pytest.mark.integration
async def test_empty_exercises_and_wide_layout(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.sections[0].study_prompts = []
    result = await render_lesson(lesson, tmp_path, layout="wide")
    assert result["answer_section_page"] == 0
    assert result["page_sizes_pt"][0][0] > 800
    assert result["blocked_requests"] == []


@pytest.mark.integration
@pytest.mark.parametrize("layout", ["a4", "wide"])
async def test_short_section_pause_stays_in_right_rail_with_completed_content(tmp_path: Path, layout):
    lesson = example_lesson(practice=False)
    result = await render_lesson(lesson, tmp_path, layout=layout)
    assert len(result["pause_positions"]) == 1
    pause = result["pause_positions"][0]
    assert pause["kind"] == "end" and pause["boundary"] == "example"
    assert pause["right_rail"] and pause["at_boundary_end"]
    assert pause["attached_content_characters"] > 20 and not pause["orphaned"]
    assert result["pause_plan"][0]["basis"] == "legacy_author"
    assert result["pause_plan"][0]["estimated_minutes_since_break"] is None
    assert result["pause_only_pages"] == [] and result["content_preserved"]


@pytest.mark.integration
async def test_task_plan_selects_same_break_boundaries_in_a4_and_wide(tmp_path: Path):
    lesson = example_lesson(long=True)
    lesson.sections[0].worked_example += "\n\n" + ("完整例题过程：先确定条件，再计算分子，最后解释结果。" * 70)
    lesson.sections[0].pause.resume = "END_ONLY_RESUME：完成本节练习后再复习。"
    lesson.sections[0].study_load = StudyLoad(
        explanation_minutes=25, worked_example_minutes=10, practice_minutes=15,
        rationale="Fixture task estimates include reconstruction of the explanation and independent practice.",
    )
    results = []
    for layout in ["a4", "wide"]:
        directory = tmp_path / layout
        result = await render_lesson(lesson, directory, layout=layout)
        results.append(result)
        pauses = result["pause_positions"]
        assert len(pauses) == 2
        middle, end = pauses
        assert middle["kind"] == "middle" and middle["boundary"] == "explanation"
        assert end["kind"] == "end" and end["boundary"] == "practice-1"
        assert middle["page"] < end["page"]
        assert all(point["estimated_minutes_since_break"] == 25 for point in result["pause_plan"])
        assert all(point["basis"] == "content_estimate" for point in result["pause_plan"])
        assert all(pause["right_rail"] and pause["at_boundary_end"] and not pause["orphaned"] for pause in pauses)
        reader = PdfReader(directory / "lesson.pdf")
        middle_text = comparable_pdf_text(reader.pages[middle["page"] - 1].extract_text())
        end_text = comparable_pdf_text(reader.pages[end["page"] - 1].extract_text())
        assert "END_ONLY_RESUME" not in middle_text and "END_ONLY_RESUME" in end_text
        assert "25" in middle_text and "5" in middle_text
        assert result["content_preserved"] and not result["overflow"]
        assert len(result["sidebar_navigation"]) == 1
        assert result["orphan_heading_pages"] == [] and result["pause_only_pages"] == []
    assert results[0]["page_count"] != results[1]["page_count"]
    assert results[0]["pause_plan"] == results[1]["pause_plan"]


@pytest.mark.integration
async def test_old_long_lesson_without_pause_does_not_invent_breaks(tmp_path: Path):
    lesson = example_lesson(long=True, practice=False)
    lesson.sections[0].pause = None
    lesson.text.pause_when = "FALLBACK_WHEN: If your timer has rung, take a short break."
    lesson.text.pause_activity = "FALLBACK_ACTIVITY: Stand up and get water."
    lesson.text.pause_resume = "FALLBACK_RESUME: Recall the relation you just read."
    result = await render_lesson(lesson, tmp_path)
    assert lesson.sections[0].pause is None
    assert result["pause_positions"] == [] and result["pause_plan"] == []
    text = "".join(page.extract_text() for page in PdfReader(tmp_path / "lesson.pdf").pages)
    assert all(marker not in text for marker in ["FALLBACK_WHEN", "FALLBACK_ACTIVITY", "FALLBACK_RESUME"])
    assert result["content_preserved"]


@pytest.mark.integration
async def test_short_practice_group_does_not_add_a_second_break(tmp_path: Path):
    lesson = example_lesson(long=True)
    section = lesson.sections[0]
    section.practice.append(Practice(id="P2", prompt="复述例题中确定分母的办法。", hint="沿用条件事件。", answer="只数条件成立的人。"))
    section.study_load = StudyLoad(
        explanation_minutes=12, worked_example_minutes=10, practice_minutes=3,
        rationale="两个简短同类练习合计约 3 分钟，与讲解和例题组成一个任务组。",
    )
    result = await render_lesson(lesson, tmp_path)
    assert len(result["pause_positions"]) == 1
    assert result["pause_positions"][0]["boundary"] == "practice-2"
    assert result["pause_plan"][0]["estimated_minutes_since_break"] == 25
    assert result["content_preserved"] and not result["overflow"]


@pytest.mark.integration
async def test_only_selected_pause_text_is_rendered_and_math_checked(tmp_path: Path):
    lesson = example_lesson()
    lesson.sections[0].study_load = StudyLoad(
        explanation_minutes=6, worked_example_minutes=5, practice_minutes=3,
        rationale="A short lesson does not reach a planned break opportunity.",
    )
    lesson.sections[0].pause.resume = r"UNUSED_PAUSE: $\NotARealCommand{a}$"
    result = await render_lesson(lesson, tmp_path)
    assert result["pause_positions"] == []
    assert result["math_count"] == 1
    html = BeautifulSoup((tmp_path / "lesson.html").read_text(encoding="utf-8"), "html.parser")
    assert not html.select(".pause-source") and "UNUSED_PAUSE" not in html.get_text()


@pytest.mark.integration
@pytest.mark.parametrize("repetitions", [14, 20, 60, 100])
async def test_pause_with_taller_existing_sidebar_preserves_question_and_return_target(tmp_path: Path, repetitions):
    lesson = example_lesson(practice=False)
    lesson.sections[0].study_prompts.append(StudyPrompt(
        id="S2", kind="question", when="读完完整例题", task="TALL_QUESTION: " + "Describe each denominator term. " * repetitions,
        check="检查完整解释是否保留。", answer="TALL_ANSWER：分母表示条件事件。",
    ))
    result = await render_lesson(lesson, tmp_path)
    assert len(result["sidebar_navigation"]) == 2
    assert result["content_preserved"] and result["overflow"] == []
    assert all(not pause["orphaned"] and pause["at_boundary_end"] for pause in result["pause_positions"])
    text = "".join(page.extract_text() for page in PdfReader(tmp_path / "lesson.pdf").pages)
    assert "TALL_QUESTION" in text and "TALL_ANSWER" in text
    rail = sidebar_pdf_text(PdfReader(tmp_path / "lesson.pdf"), result["sidebar_navigation"][1]["prompt"]["left_pt"])
    assert rail.count("Describeeachdenominatorterm.") == repetitions
    assert result["pause_positions"][0]["attached_content_characters"] > 0


@pytest.mark.integration
async def test_long_sidebar_keeps_formula_only_main_intact_with_final_pause(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.sections[0].worked_example = r"$$P(A\mid B)=\frac{P(A\cap B)}{P(B)}.$$"
    lesson.sections[0].study_prompts.append(StudyPrompt(
        id="S2", kind="question", when="After the example", task="FORMULA_QUESTION: " + "Describe each denominator term. " * 100,
        check="Check all terms.", answer="FORMULA_ANSWER: The denominator is the given event.",
    ))
    result = await render_lesson(lesson, tmp_path)
    assert result["content_preserved"] and result["overflow"] == []
    assert result["math_count"] == 2
    assert len(result["sidebar_navigation"]) == 2
    assert all(not pause["orphaned"] and pause["at_boundary_end"] for pause in result["pause_positions"])
    rail = sidebar_pdf_text(PdfReader(tmp_path / "lesson.pdf"), result["sidebar_navigation"][1]["prompt"]["left_pt"])
    assert rail.count("Describeeachdenominatorterm.") == 100


@pytest.mark.integration
async def test_wide_formula_uses_full_a4_width(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.sections[0].worked_example += (
        r" $$P(A\mid B)+P(C\mid B)+P(D\mid B)+P(E\mid B)=\frac{P(A\cap B)+P(C\cap B)}{P(B)}.$$"
    )
    result = await render_lesson(lesson, tmp_path)
    assert result["overflow"] == []
    assert result["math_count"] == 2


@pytest.mark.integration
async def test_sidebar_only_answers_have_forward_and_return_navigation(tmp_path: Path):
    lesson = example_lesson(practice=False)
    result = await render_lesson(lesson, tmp_path)
    assert result["answer_section_page"] > 0
    html = BeautifulSoup((tmp_path / "lesson.html").read_text(encoding="utf-8"), "html.parser")
    assert html.select_one('a[href="#prompt-answer-1-1"]')
    assert html.select_one('#prompt-answer-1-1 a[href="#prompt-1-1"]')
    assert result["internal_links"] >= 6
    pair = result["sidebar_navigation"][0]
    assert pair["prompt"]["page"] in pair["answer"]["linked_from_pages"]
    assert pair["answer"]["page"] in pair["prompt"]["linked_from_pages"]
    assert pair["prompt"]["left_pt"] > 350  # right rail, not the section heading on the left
    for target in pair.values():
        assert abs(target["left_pt"] - target["expected_left_pt"]) <= 2
        assert abs(target["top_pt"] - target["expected_top_pt"]) <= 2


@pytest.mark.integration
async def test_operation_prompt_does_not_create_an_answer_section(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.sections[0].study_prompts[0] = StudyPrompt(
        id="S1", kind="action", when="读完例题后", task="记录当前页码，安排明天复习。", check="明天从记录的页码继续。", answer=None
    )
    result = await render_lesson(lesson, tmp_path)
    assert result["answer_section_page"] == 0


@pytest.mark.integration
async def test_long_overview_does_not_strand_navigation_or_leave_title_tex(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.overview.concepts = [Concept(
        name=r"交事件 $A\cap B$", explanation="先确定参照范围，再识别同时满足条件的对象。" * 4,
        connections="它连接条件概率公式的分子与独立性的判定。" * 3,
    ) for _ in range(7)]
    lesson.overview.learning_path = ["先确认条件事件，再选择对应的分母进行计算。"] * 6
    result = await render_lesson(lesson, tmp_path)
    assert result["math_count"] == 8
    assert result["navigation_only_pages"] == []
    assert result["content_preserved"]


@pytest.mark.integration
async def test_multiple_materials_show_roles_comparisons_and_human_locations(tmp_path: Path):
    lesson = example_lesson(practice=False)
    lesson.sections[0].worked_example += r" $$P(A)=\frac{12}{30},\qquad P(B)=\frac{10}{30},\qquad P(A\cap B)=\frac{6}{30}.$$"
    reference_ref, topic_ref = "a" * 32 + ":3", "b" * 32 + ":2"
    lesson.sources.extend([
        SourceCitation(ref=reference_ref, document="教材补充.pdf", label="PDF 第 3 页（印刷第 5 页）", role="reference"),
        SourceCitation(ref=topic_ref, document="课堂笔记.docx", label="第 2 内容段", role="topic"),
    ])
    lesson.sections[0].source_refs.extend([reference_ref, topic_ref])
    lesson.sections[0].source_notes = [
        SourceNote(ref="D1:1", explanation="主材料通过筛选戴眼镜的同学，直观说明条件事件改变参照范围。",
                   relation="给出计算步骤；下面两份资料分别补充公式前提与常见混淆。"),
        SourceNote(ref=reference_ref, explanation=r"教材强调 $P(B)>0$ 是条件概率公式的前提。",
                   relation="与主材料一致，补充其公式可用条件。"),
        SourceNote(ref=topic_ref, explanation="笔记比较先筛选 A 与先筛选 B 的分母；<script>INERT_MARKER</script>只作文本。",
                   relation="两个条件概率的方向不同；这是补充辨析，不是与主材料冲突。"),
    ]
    result = await render_lesson(lesson, tmp_path)
    reader = PdfReader(tmp_path / "lesson.pdf")
    text = comparable_pdf_text("".join("".join(page.extract_text().split()) for page in reader.pages))
    assert "资料对照" in text and "主材料通过筛选戴眼镜" in text
    assert normalize("NFKC", "与主材料一致，补充其公式可用条件") in text
    assert "教材补充.pdf" in text and normalize("NFKC", "PDF第3页（印刷第5页）") in text
    assert "课堂笔记.docx" in text and "第2内容段" in text
    assert "查看原材料" not in text  # source-opening controls belong to the hosted Web reader only
    assert all(role in text for role in ["主材料", "参考资料", "主题相关资料"])
    assert reference_ref not in text and topic_ref not in text and "D1:1" not in text
    assert text.index("资料对照") < text.index("完整例题")
    assert result["content_preserved"] and not result["browser_errors"]
    assert result["math_count"] == 3
    html = BeautifulSoup((tmp_path / "lesson.html").read_text(encoding="utf-8"), "html.parser")
    assert len(html.select('.source-note a[href="#source-2"]')) == 1
    assert "INERT_MARKER" in html.get_text()
    buttons = html.select('button.web-source-button[type="button"][data-source-ref]')
    assert len(buttons) == 9
    assert {button["data-source-ref"] for button in buttons} == {"D1:1", reference_ref, topic_ref}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 360, "height": 780})
            await page.goto((tmp_path / "lesson.html").as_uri())
            await page.wait_for_function("window.learnmarginReport !== undefined")
            button = page.locator(".web-source-button").first
            assert not await button.is_visible()
            await page.evaluate("document.documentElement.classList.add('embedded-reader')")
            assert await button.is_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.emulate_media(media="print")
            assert not await button.is_visible()
        finally:
            await browser.close()


def test_comparison_cannot_reference_an_unprovided_or_unassigned_source():
    lesson = example_lesson()
    lesson.sections[0].source_notes = [SourceNote(ref="missing:1", explanation="不能凭空引入来源。", relation="未知出处。")]
    with pytest.raises(RenderError, match="资料对照引用"):
        build_html(lesson)
    lesson.sources.append(SourceCitation(ref="missing:1", document="额外材料", label="第 1 页"))
    with pytest.raises(RenderError, match="资料对照引用"):
        build_html(lesson)
