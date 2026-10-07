"""Offline, measured PDF layout with adjacent study prompts and local mathematics."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from markupsafe import Markup
from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject

from .models import Lesson
from .study_rhythm import plan_pauses

ROOT = Path(__file__).parent
NAVIGATION_ZOOM = 1.25


class RenderError(ValueError):
    """A lesson cannot be delivered without losing or misrendering content."""


def _math_inline(state: Any, silent: bool) -> bool:
    start = state.pos
    source = state.src
    pair = next(((left, right) for left, right in [("\\(", "\\)"), ("\\[", "\\]"),
                                                  ("$$", "$$"), ("$", "$")]
                 if source.startswith(left, start)), None)
    if pair is None:
        return False
    left, right = pair
    end = source.find(right, start + len(left))
    if end < 0 or end == start + len(left):
        return False
    if not silent:
        token = state.push("math", "", 0)
        token.content = source[start + len(left):end]
        token.meta = {"display": left in {"\\[", "$$"}}
    state.pos = end + len(right)
    return True


def _math_block(state: Any, start: int, end: int, silent: bool) -> bool:
    line = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]].strip()
    left = "$$" if line.startswith("$$") else "\\[" if line.startswith("\\[") else None
    if left is None:
        return False
    right = "$$" if left == "$$" else "\\]"
    parts = [line[len(left):]]
    next_line = start + 1
    while right not in parts[-1] and next_line < end:
        parts.append(state.src[state.bMarks[next_line]:state.eMarks[next_line]])
        next_line += 1
    joined = "\n".join(parts)
    close = joined.find(right)
    if close < 0 or joined[close + len(right):].strip():
        return False
    if not silent:
        token = state.push("math", "", 0)
        token.block = True
        token.content = joined[:close].strip()
        token.meta = {"display": True}
        token.map = [start, next_line]
    state.line = next_line
    return True


def _math_html(tokens: Any, idx: int, *_: Any) -> str:
    token = tokens[idx]
    tag = "div" if token.block else "span"
    display = "true" if token.meta["display"] else "false"
    tex = escape(token.content, quote=True)
    return f'<{tag} class="math" data-display="{display}" data-tex="{tex}">{tex}</{tag}>'


def markdown(text: str, *, inline: bool = False, image_label: str = "图示") -> Markup:
    """Accept Markdown/TeX, never executable HTML, external media or active links."""
    parser = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable("table")
    parser.inline.ruler.before("escape", "math", _math_inline)
    parser.block.ruler.before("paragraph", "math_block", _math_block)
    parser.renderer.rules["math"] = _math_html
    parser.renderer.rules["link_open"] = lambda *_: '<span class="source-link">'
    parser.renderer.rules["link_close"] = lambda *_: "</span>"
    parser.renderer.rules["image"] = lambda tokens, idx, *_: (
        '<span class="image-description">[' + escape(image_label) + ": " + escape(tokens[idx].content) + "]</span>"
    )
    return Markup(parser.renderInline(text) if inline else parser.render(text))


@lru_cache(maxsize=1)
def _math_assets() -> tuple[str, str]:
    assets = ROOT / "assets" / "katex"
    css = (assets / "katex.min.css").read_text(encoding="utf-8")

    def replace_font(match: re.Match[str]) -> str:
        font = assets / "fonts" / match.group(1)
        # Only WOFF2 is needed by Chromium. Remove fallback URLs from the stylesheet below.
        data = base64.b64encode(font.read_bytes()).decode("ascii")
        return f'url(data:font/woff2;base64,{data}) format("woff2")'

    css = re.sub(r'url\(fonts/([^()]+\.woff2)\) format\("woff2"\)', replace_font, css)
    css = re.sub(r',url\(fonts/[^()]+\.(?:woff|ttf)\) format\("[^"()]+"\)', "", css)
    script = (assets / "katex.min.js").read_text(encoding="utf-8")
    return css, script.replace("</script", "<\\/script")


def build_html(lesson: Lesson, *, layout: str = "a4") -> str:
    if layout not in {"a4", "wide"}:
        raise RenderError("未知版式；请选择 a4 或 wide。")
    if not lesson.sections:
        raise RenderError("讲义至少需要一个正文小节。")
    source_refs = {source.ref: index + 1 for index, source in enumerate(lesson.sources)}
    for section in lesson.sections:
        if any(ref not in source_refs for ref in section.source_refs):
            raise RenderError("讲义引用了不存在的材料位置，请重新生成。")
        if any(note.ref not in source_refs or note.ref not in section.source_refs for note in section.source_notes):
            raise RenderError("资料对照引用的位置不存在，或未列入当前章节来源。")
    css, katex = _math_assets()
    paginator = (ROOT / "templates" / "paginate.js").read_text(encoding="utf-8")
    # The portable HTML must only execute the two bundled scripts. Hashes cover
    # the exact script text emitted below; model content never enters this list.
    script_sources = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii") + "'"
        for script in (katex, paginator)
    )
    environment = Environment(
        loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "xml"])
    )
    environment.filters["md"] = lambda value: markdown(value, image_label=lesson.text.image)
    environment.filters["mdi"] = lambda value: markdown(value, inline=True, image_label=lesson.text.image)
    pause_plan = plan_pauses(lesson)
    fallback_pause = {"minutes": 5, "when": lesson.text.pause_when,
                      "activity": lesson.text.pause_activity, "resume": lesson.text.pause_resume}
    pause_cards: dict[int, dict[str, dict[str, Any]]] = {}
    for point in pause_plan:
        section = lesson.sections[point["section"] - 1]
        pause = section.pause if point["kind"] == "end" and section.pause else fallback_pause
        pause_cards.setdefault(point["section"], {})[point["boundary"]] = {"kind": point["kind"], "pause": pause}
    prompt_cards: dict[int, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    for section_no, section in enumerate(lesson.sections, 1):
        for prompt_no, prompt in enumerate(section.study_prompts, 1):
            placement = prompt.placement
            if placement is None:
                # Preserve the original positions and answer IDs in saved lessons.
                boundary, edge = ("explanation" if prompt_no == 1 else "example"), "start"
            else:
                edge = "start" if placement.startswith("before_") else "end"
                block = placement.split("_", 1)[1]
                boundary = {"explanation": "explanation", "example": "example",
                            "practice": f"practice-{1 if edge == 'start' else len(section.practice)}"}[block]
            group = prompt_cards.setdefault(section_no, {}).setdefault(boundary, {"start": [], "end": []})
            group[edge].append({"prompt": prompt, "number": prompt_no})
    return environment.get_template("lesson.html").render(
        lesson=lesson, text=lesson.text, layout=layout, page_width=210 if layout == "a4" else 286,
        pause_plan=pause_plan, pause_cards=pause_cards, prompt_cards=prompt_cards,
        has_answers=any(section.practice or any(prompt.answer for prompt in section.study_prompts)
                        for section in lesson.sections),
        source_refs=source_refs, katex_css=Markup(css), katex_script=Markup(katex), script_sources=script_sources,
        role_labels={"primary": lesson.text.source_primary, "reference": lesson.text.source_reference,
                     "topic": lesson.text.source_topic},
        stylesheet=Markup((ROOT / "templates" / "lesson.css").read_text(encoding="utf-8")),
        paginator=Markup(paginator),
    )


def _set_navigation_zoom(path: Path, anchors: dict[str, Any]) -> None:
    """Keep Chromium's anchor coordinates and pages, but leave fit-page view on a jump."""
    writer = PdfWriter(clone_from=path)

    def entries():
        direct = writer.root_object.get("/Dests")
        if direct is not None:
            yield from direct.get_object().items()
        names = writer.root_object.get("/Names")
        if names is None:
            return
        tree = names.get_object().get("/Dests")
        if tree is None:
            return

        def walk(node):
            node = node.get_object()
            pairs = node.get("/Names", [])
            yield from zip(pairs[::2], pairs[1::2])
            for child in node.get("/Kids", []):
                yield from walk(child)

        yield from walk(tree)

    try:
        for name, destination in entries():
            if str(name).lstrip("/") not in anchors:
                continue
            destination = destination.get_object()
            if isinstance(destination, DictionaryObject):
                destination = destination["/D"]
            if (not isinstance(destination, ArrayObject) or len(destination) != 5
                    or destination[1] != "/XYZ"):
                raise RenderError("PDF 导航目标缺少精确阅读位置，未导出。")
            destination[4] = FloatObject(NAVIGATION_ZOOM)
        temporary = path.with_suffix(".navigation.pdf")
        writer.write(temporary)
        temporary.replace(path)
    finally:
        writer.close()


def _pdf_report(path: Path, expected_pages: int, layout: str, expected_links: int,
                anchors: dict[str, Any]) -> dict[str, Any]:
    reader = PdfReader(path)
    expected_width = (210 if layout == "a4" else 286) / 25.4 * 72
    sizes = [[round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2)]
             for page in reader.pages]
    if len(reader.pages) != expected_pages:
        raise RenderError("PDF 分页数量与预览不一致，未交付可能截断的讲义。")
    if any(abs(width - expected_width) > 1 or abs(height - 297 / 25.4 * 72) > 1
           for width, height in sizes):
        raise RenderError("PDF 页面尺寸与选择的版式不一致。")
    text_lengths = [len((page.extract_text() or "").strip()) for page in reader.pages]
    if any(length < 8 for length in text_lengths):
        raise RenderError("PDF 中存在空白页或无法提取文字的页面。")
    links = 0
    incoming: dict[str, list[int]] = {}
    page_references = {(page.indirect_reference.idnum, page.indirect_reference.generation)
                       for page in reader.pages if page.indirect_reference is not None}
    for page_number, page in enumerate(reader.pages, 1):
        for annotation in page.get("/Annots", []):
            item = annotation.get_object()
            if item.get("/Subtype") == "/Link":
                action = item.get("/A", {})
                destination = item.get("/Dest")
                if destination is None and action.get("/S") == "/GoTo":
                    destination = action.get("/D")
                if destination is None:
                    raise RenderError("PDF 包含非预期的外部链接。")
                if isinstance(destination, str):
                    named = reader.named_destinations.get(destination)
                    if named is None or reader.get_destination_page_number(named) is None:
                        raise RenderError("PDF 命名导航目标不存在。")
                    incoming.setdefault(destination.lstrip("/"), []).append(page_number)
                else:
                    first = destination[0]
                    if not hasattr(first, "idnum") or (first.idnum, first.generation) not in page_references:
                        raise RenderError("PDF 内部跳转没有指向有效页面。")
                links += 1
    if links < expected_links:
        raise RenderError("PDF 内部导航有缺失，请检查练习与答案跳转。")
    navigation_targets = []
    destinations = reader.named_destinations
    for anchor_id, position in anchors.items():
        target = destinations.get("/" + anchor_id) or destinations.get(anchor_id)
        if target is None and not incoming.get(anchor_id):
            continue
        if (target is None or reader.get_destination_page_number(target) + 1 != position["page"]
                or target.typ != "/XYZ" or target.left is None or target.top is None or target.zoom is None
                or abs(float(target.left) - position["left"]) > 2
                or abs(float(target.top) - position["top"]) > 2
                or abs(float(target.zoom) - NAVIGATION_ZOOM) > .001):
            raise RenderError("PDF 内部导航未保留目标位置或 125% 阅读缩放。")
        navigation_targets.append({"id": anchor_id, "page": position["page"],
                                   "left_pt": float(target.left), "top_pt": float(target.top),
                                   "zoom": float(target.zoom), "linked_from_pages": incoming.get(anchor_id, [])})
    sidebar_navigation = []
    for anchor_id, expected in anchors.items():
        if not anchor_id.startswith("prompt-answer-"):
            continue
        prompt_id = anchor_id.replace("prompt-answer-", "prompt-", 1)
        pair = {}
        for role, target_id in [("prompt", prompt_id), ("answer", anchor_id)]:
            target = destinations.get("/" + target_id) or destinations.get(target_id)
            position = anchors[target_id]
            if (target is None or not incoming.get(target_id)
                    or reader.get_destination_page_number(target) + 1 != position["page"]
                    or target.left is None or target.top is None
                    or abs(float(target.left) - position["left"]) > 2
                    or abs(float(target.top) - position["top"]) > 2):
                raise RenderError("侧栏与答案的双向导航未指向对应卡片位置。")
            pair[role] = {"id": target_id, "page": position["page"],
                          "left_pt": float(target.left), "top_pt": float(target.top),
                          "zoom": float(target.zoom),
                          "expected_left_pt": round(position["left"], 3),
                          "expected_top_pt": round(position["top"], 3),
                          "linked_from_pages": incoming[target_id]}
        sidebar_navigation.append(pair)
    return {"page_count": len(reader.pages), "page_sizes_pt": sizes,
            "text_characters_per_page": text_lengths, "internal_links": links,
            "sidebar_navigation": sidebar_navigation, "navigation_zoom": NAVIGATION_ZOOM,
            "navigation_targets": navigation_targets}


async def render_lesson(lesson: Lesson, output_dir: Path, *, layout: str = "a4") -> dict[str, Any]:
    """Write portable HTML, PDF, editable data and checked layout evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "lesson.html"
    pdf_path = output_dir / "lesson.pdf"
    html_path.write_text(build_html(lesson, layout=layout), encoding="utf-8")
    (output_dir / "lesson.json").write_text(lesson.model_dump_json(indent=2), encoding="utf-8")
    blocked: list[str] = []
    errors: list[str] = []
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        except Exception as exc:
            raise RenderError(
                "无法启动启用沙箱的 PDF 引擎。请确认已执行 python -m playwright install chromium，"
                "并安装浏览器系统依赖、允许 Chromium 沙箱；不会改用无沙箱模式。"
            ) from exc
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 1000})
            async def intercept(route: Any) -> None:
                url = route.request.url
                if url.startswith("data:") or url.split("#", 1)[0] == html_path.resolve().as_uri():
                    await route.continue_()
                else:
                    blocked.append(url[:200])
                    await route.abort()
            await page.route("**/*", intercept)
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(html_path.resolve().as_uri(), wait_until="load")
            await page.wait_for_function("window.learnmarginReport !== undefined", timeout=60_000)
            report = await page.evaluate("window.learnmarginReport")
            if report.get("error") or errors:
                raise RenderError("PDF 排版检查失败：" + str(report.get("error") or errors[0]))
            if report["overflow"]:
                raise RenderError("发现内容超出页面边界，请缩短过宽的公式或重新分段。")
            if blocked:
                raise RenderError("讲义触发了外部资源请求，已阻止。")
            await page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True,
                           tagged=True, outline=True)
        finally:
            await browser.close()
    _set_navigation_zoom(pdf_path, report["anchor_positions"])
    pdf = _pdf_report(pdf_path, report["page_count"], layout, report["internal_links"], report["anchor_positions"])
    validation = {**pdf, "layout": layout, "math_count": report["math_count"], "math_errors": [],
                  "overflow": [], "blocked_requests": blocked, "browser_errors": errors,
                  "content_preserved": report["content_preserved"],
                  "answer_section_page": report["answer_section_page"],
                  "navigation_only_pages": report["navigation_only_pages"],
                  "orphan_heading_pages": report["orphan_heading_pages"],
                  "pause_only_pages": report["pause_only_pages"],
                  "pause_positions": report["pause_positions"], "pause_plan": report["pause_plan"],
                  "limitations": "已检查内容保留、分页、公式排版与导航；未验证学科正确性或实际学习效果。"}
    (output_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return validation
