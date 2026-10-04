from __future__ import annotations

import json

import pytest

from learnmargin import pipeline
from learnmargin.models import APIConfig, Document, GenerateRequest, Scope, SourceUnit
from learnmargin.pipeline import generate_lesson, make_units, parse_range, source_content
from learnmargin.storage import Store

DOC_ID = "a" * 32


@pytest.mark.parametrize("text,total,expected", [
    ("1", 1, [1]), ("1-3,5", 5, [1, 2, 3, 5]), ("3，1～2；2", 3, [1, 2, 3]),
    (" 2 – 4 , 1 ", 4, [1, 2, 3, 4]), ("1~1", 1, [1]),
])
def test_range_parsing_accepts_boundaries_and_deduplicates(text, total, expected):
    assert parse_range(text, total) == expected


@pytest.mark.parametrize("text,total", [
    ("", 4), (" ", 4), ("0", 4), ("-1", 4), ("3-2", 4), ("1-5", 4),
    ("1,", 4), ("1,,2", 4), ("one", 4), ("1.5", 4), ("1", 0),
])
def test_range_parsing_rejects_invalid_or_outside_values(text, total):
    with pytest.raises(ValueError):
        parse_range(text, total)


def document(*, images=False, empty=False):
    return Document(id=DOC_ID, name="自生成材料.pdf", kind="pdf", unit_label="页", warnings=["测试材料警告"],
                    units=[SourceUnit(index=index, label=f"第 {index} 页",
                                      text="" if empty else "条件概率是在已知事件范围内重新计算比例。" * 4,
                                      image_paths=["page.png"] if images else []) for index in (1, 2)])


def plan(*, missing=False, invalid=False, duplicate=False):
    first = f"{DOC_ID}:1"
    second = "forged:99" if invalid else first if missing else f"{DOC_ID}:2"
    return {"title": "条件概率", "subtitle": "独立性与分母", "overview": {
        "summary": "条件概率改变参考范围；独立性检验条件是否改变概率。",
        "concepts": [{"name": "条件概率", "explanation": "在已知事件内计算比例。", "connections": "结合独立性检查。"}],
        "learning_path": ["理解分母", "比较概率"]},
        "sections": [{"id": "s1", "title": "条件概率", "objective": "理解分母", "source_refs": [first]},
                     {"id": "s1" if duplicate else "s2", "title": "独立性", "objective": "比较概率", "source_refs": [second]}],
        "review_plan": ["明天不看讲义重新解释分母。"], "method_chapters": [3, 4]}


def section(index, *, reference=None, duplicate_prompt=False):
    return {"id": f"s{index}", "title": "条件概率" if index == 1 else "独立性",
            "source_refs": [reference or f"{DOC_ID}:{index}"],
            "explanation": "这里讨论条件如何改变参考范围，以及独立性需要满足什么等式。" * 3,
            "worked_example": "补充例题：从十个对象中选出满足条件的四个，再计算目标比例。" * 2,
            "practice": [], "study_prompts": [{"id": "shared" if duplicate_prompt else f"s{index}-a1",
                "kind": "action", "when": "完成例题后", "task": "遮住例题，重做一次后再核对。",
                "check": "对照讲义标记第一处不一致的步骤。"}]}


def review_draft(schema, user):
    marker = "待审校初稿JSON："
    if schema.__name__ == "LessonSection" and marker in user:
        return json.JSONDecoder().raw_decode(user.split(marker, 1)[1].lstrip())[0]
    return None


class SequenceProvider:
    """Deterministic in-process model substitute; never creates an HTTP client."""
    def __init__(self, values, *, revise=None):
        self.values = list(values)
        self.calls = []
        self.reviews = []
        self.revise = revise

    async def generate(self, schema, system, user, images=None):
        self.calls.append((schema.__name__, user, images))
        draft = review_draft(schema, user)
        if draft is not None:
            self.reviews.append((draft.copy(), user, images))
            return schema.model_validate(self.revise(draft) if self.revise else draft)
        if not self.values:
            raise AssertionError("Unexpected extra model request")
        return schema.model_validate(self.values.pop(0))


@pytest.fixture
def workspace(tmp_path):
    return Store(tmp_path / "data"), tmp_path / "output"


def request(**kwargs):
    return GenerateRequest(document_ids=[DOC_ID], section_count=2,
                           api=APIConfig(vision=False), **kwargs)


async def test_generation_preserves_scope_sources_and_adds_conditional_rest(workspace):
    store, output = workspace
    provider = SequenceProvider([plan(), section(1), section(2)])
    progress = []
    lesson = await generate_lesson(request(), [document()], store, output, provider,
                                   lambda label, value: progress.append(value))
    assert [source.ref for source in lesson.sources] == [f"{DOC_ID}:1", f"{DOC_ID}:2"]
    assert [item.id for item in lesson.sections] == ["s1", "s2"]
    assert lesson.warnings == ["测试材料警告"]
    assert len([item for item in lesson.sections if item.pause]) == 1
    pause = next(item.pause for item in lesson.sections if item.pause)
    assert pause.minutes == 5 and "若" in pause.when and "25分钟" in pause.when
    assert pause.resume
    assert (output / "selection.json").is_file()
    assert (output / "plan.json").is_file()
    assert (output / "section-2.json").is_file()
    assert progress == sorted(progress)


async def test_topic_selection_cannot_forge_source_locations(workspace):
    store, output = workspace
    provider = SequenceProvider([{"refs": ["invented:42"], "explanation": "伪造位置"}])
    with pytest.raises(ValueError, match="不存在"):
        await generate_lesson(request(scope=Scope(mode="topics", topics="条件概率")),
                              [document()], store, output, provider, lambda *_: None)
    assert len(provider.calls) == 1
    assert not (output / "plan.json").exists()


async def test_topic_selection_with_no_matches_stops_before_generation(workspace):
    store, output = workspace
    provider = SequenceProvider([{"refs": [], "explanation": "材料没有此知识点"}])
    with pytest.raises(ValueError, match="未能"):
        await generate_lesson(request(scope=Scope(mode="topics", topics="无关主题")),
                              [document()], store, output, provider, lambda *_: None)
    assert len(provider.calls) == 1


async def test_topic_selection_deduplicates_verified_refs(workspace):
    store, output = workspace
    provider = SequenceProvider([{"refs": [f"{DOC_ID}:1", f"{DOC_ID}:2", f"{DOC_ID}:1"],
                                  "explanation": "这两页分别讲定义与独立性。"}, plan(), section(1), section(2)])
    lesson = await generate_lesson(request(scope=Scope(mode="topics", topics="条件概率")),
                                   [document()], store, output, provider, lambda *_: None)
    assert len(lesson.sources) == 2
    assert "条件概率" in lesson.scope_note
    assert "2" in lesson.scope_note
    assert {source.ref for source in lesson.sources} == {f"{DOC_ID}:1", f"{DOC_ID}:2"}
    assert "这两页分别讲定义与独立性" not in lesson.scope_note


async def test_page_scope_requires_at_least_one_primary_range(workspace):
    store, output = workspace
    provider = SequenceProvider([])
    with pytest.raises(ValueError, match="范围|主资料"):
        await generate_lesson(request(scope=Scope(mode="pages")), [document()], store, output,
                              provider, lambda *_: None)
    assert not provider.calls


@pytest.mark.parametrize("fault", ["missing", "invalid", "duplicate"])
async def test_plan_with_incomplete_or_invalid_coverage_gets_bounded_repairs(workspace, fault):
    store, output = workspace
    wrong = plan(**{fault: True})
    provider = SequenceProvider([wrong, wrong, wrong])
    with pytest.raises(ValueError, match="计划"):
        await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)
    assert len(provider.calls) == 3
    assert not (output / "plan.json").exists()


async def test_successful_final_plan_repair_is_actually_validated(workspace):
    store, output = workspace
    provider = SequenceProvider([plan(missing=True), plan(missing=True), plan(), section(1), section(2)])
    lesson = await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)
    assert len(lesson.sources) == 2
    assert len(lesson.sections) == 2
    assert [name for name, *_ in provider.calls].count("LessonPlan") == 3
    assert [name for name, *_ in provider.calls].count("LessonSection") == 4


async def test_plan_must_honor_requested_number_of_sections(workspace):
    store, output = workspace
    provider = SequenceProvider([plan(), plan(), plan()])
    four_sections = GenerateRequest(document_ids=[DOC_ID], section_count=4, api=APIConfig(vision=False))
    with pytest.raises(ValueError, match="计划|章节"):
        await generate_lesson(four_sections, [document()], store, output, provider, lambda *_: None)
    assert len(provider.calls) == 3


async def test_section_cannot_change_approved_reference(workspace):
    store, output = workspace
    provider = SequenceProvider([plan(), section(1, reference="invented:42"), section(2)])
    with pytest.raises(ExceptionGroup) as caught:
        await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)
    assert any(isinstance(error, ValueError) and "引用" in str(error) for error in caught.value.exceptions)


async def test_duplicate_prompt_identifiers_fail_before_rendering(workspace):
    store, output = workspace
    provider = SequenceProvider([plan(), section(1, duplicate_prompt=True), section(2, duplicate_prompt=True)])
    with pytest.raises(ValueError, match="重复"):
        await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)


async def test_scanned_pages_require_vision_and_never_get_silently_omitted(workspace):
    store, output = workspace
    provider = SequenceProvider([])
    with pytest.raises(ValueError, match="扫描页"):
        await generate_lesson(request(), [document(images=True, empty=True)], store, output,
                              provider, lambda *_: None)
    assert not provider.calls


def test_source_images_stay_inside_document_directory(tmp_path):
    store = Store(tmp_path / "data")
    doc = document(images=True)
    folder = store.directory("documents", DOC_ID)
    folder.mkdir()
    (folder / "page.png").write_bytes(b"local image fixture")
    content, images = source_content(make_units([doc]), store, True)
    assert all(path.parent == folder for path in images)
    assert json.loads(content)[1]["attached_image_numbers"] == [2]
    doc.units[0].image_paths = ["../outside.png"]
    (folder.parent / "outside.png").write_bytes(b"must not read")
    with pytest.raises(ValueError, match="路径无效"):
        source_content(make_units([doc]), store, True)


async def test_material_limits_stop_before_paid_generation(workspace, monkeypatch):
    store, output = workspace
    provider = SequenceProvider([])
    monkeypatch.setattr(pipeline, "MAX_SELECTED_UNITS", 1)
    with pytest.raises(ValueError, match="内容单元"):
        await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)
    assert not provider.calls


REFERENCE_ID = "b" * 32
OTHER_REFERENCE_ID = "c" * 32


def source_document(identifier, name, texts):
    return Document(id=identifier, name=name, kind="md", unit_label="内容段", units=[
        SourceUnit(index=index, label=f"第 {index} 内容段", text=text)
        for index, text in enumerate(texts, 1)])


class IntegrationProvider:
    """Return a controlled selection and source-backed lesson, recording each stage."""
    def __init__(self, selected_refs, selections=(), *, omit_notes=False, forged_note=False,
                 reference_only_plan=False):
        self.selected_refs = selected_refs
        self.selections = list(selections)
        self.calls = []
        self.omit_notes = omit_notes
        self.forged_note = forged_note
        self.reference_only_plan = reference_only_plan

    async def generate(self, schema, system, user, images=None):
        self.calls.append((schema.__name__, user, images))
        draft = review_draft(schema, user)
        if draft is not None:
            return schema.model_validate(draft)
        if schema.__name__ == "StudyFocus":
            return schema.model_validate({"topics": ["条件概率", "分母的含义"],
                                          "summary": "主材料要求在给定条件范围内计算概率。"})
        if schema.__name__ == "TopicSelection":
            return schema.model_validate({"refs": self.selections.pop(0),
                                          "explanation": "这些位置用集合范围解释条件概率的分母。"})
        if schema.__name__ == "LessonPlan":
            result = plan()
            for item in result["sections"]:
                item["source_refs"] = (self.selected_refs[-1:] if self.reference_only_plan
                                       else self.selected_refs.copy())
            return schema.model_validate(result)
        if schema.__name__ == "LessonSection":
            planned = json.JSONDecoder().raw_decode(user.split("本节计划：", 1)[1])[0]
            result = section(int(planned["id"][1:]))
            result["source_refs"] = planned["source_refs"]
            seen_documents = set()
            result["source_notes"] = []
            for ref in planned["source_refs"]:
                identifier = ref.split(":", 1)[0]
                if identifier in seen_documents:
                    continue
                seen_documents.add(identifier)
                result["source_notes"].append({"ref": "invented:99" if self.forged_note else ref,
                    "explanation": "此处说明概率的分母来自已知条件所限制的范围。",
                    "relation": "与其他材料的定义一致，并补充集合角度的直观解释。"})
            if self.omit_notes:
                result["source_notes"] = []
            return schema.model_validate(result)
        raise AssertionError(f"Unexpected model schema: {schema.__name__}")


def multiple_request(*, mode="pages", ranges=None):
    return GenerateRequest(document_ids=[DOC_ID, REFERENCE_ID], section_count=2,
                           api=APIConfig(vision=False), scope=Scope(mode=mode,
                               ranges=ranges if ranges is not None else {DOC_ID: "2"},
                               topics="条件概率" if mode == "topics" else ""))


def multiple_documents():
    main = source_document(DOC_ID, "主课件.md", ["未选的其他知识：随机变量。", "条件概率的分母表示已知事件的范围。"])
    reference = source_document(REFERENCE_ID, "辅助教材.md", ["线性代数中的矩阵。", "条件概率可理解为把样本空间限制到B。"])
    return [main, reference]


async def test_page_scope_preserves_main_pages_and_retrieves_unranged_reference(workspace):
    store, output = workspace
    expected = [f"{DOC_ID}:2", f"{REFERENCE_ID}:2"]
    provider = IntegrationProvider(expected, selections=[[f"{REFERENCE_ID}:2"]])
    lesson = await generate_lesson(multiple_request(), multiple_documents(), store, output, provider, lambda *_: None)
    assert [source.ref for source in lesson.sources] == expected
    assert [source.role for source in lesson.sources] == ["primary", "reference"]
    assert [name for name, *_ in provider.calls].count("StudyFocus") == 1
    assert [name for name, *_ in provider.calls].count("TopicSelection") == 1
    focus_prompt = next(user for name, user, _ in provider.calls if name == "StudyFocus")
    assert "条件概率的分母" in focus_prompt
    assert "未选的其他知识" not in focus_prompt
    reference_prompt = next(user for name, user, _ in provider.calls if name == "TopicSelection")
    assert "把样本空间限制到B" in reference_prompt
    assert all({note.ref.split(":")[0] for note in item.source_notes} == {DOC_ID, REFERENCE_ID}
               for item in lesson.sections)
    reasoning = json.loads((output / "scope-reasoning.json").read_text(encoding="utf-8"))
    assert REFERENCE_ID in json.dumps(reasoning)
    assert f"{REFERENCE_ID}:2" in json.dumps(reasoning)


async def test_unrelated_reference_is_not_fabricated_or_required_in_plan(workspace):
    store, output = workspace
    provider = IntegrationProvider([f"{DOC_ID}:2"], selections=[[]])
    lesson = await generate_lesson(multiple_request(), multiple_documents(), store, output, provider, lambda *_: None)
    assert [source.ref for source in lesson.sources] == [f"{DOC_ID}:2"]
    assert all(source.role == "primary" for source in lesson.sources)
    assert any("辅助教材.md" in warning for warning in lesson.warnings)
    assert all(note.ref.startswith(DOC_ID) for item in lesson.sections for note in item.source_notes)


async def test_primary_range_is_validated_before_searching_reference(workspace):
    store, output = workspace
    provider = IntegrationProvider([])
    with pytest.raises(ValueError, match="范围"):
        await generate_lesson(multiple_request(ranges={DOC_ID: "99"}), multiple_documents(), store,
                              output, provider, lambda *_: None)
    assert not provider.calls


@pytest.mark.parametrize("ranges", [{}, {DOC_ID: " "}, {"d" * 32: "1"}])
async def test_pages_reject_empty_or_unselected_primary_ranges(workspace, ranges):
    store, output = workspace
    provider = IntegrationProvider([])
    with pytest.raises(ValueError, match="范围|材料|资料"):
        await generate_lesson(multiple_request(ranges=ranges), multiple_documents(), store,
                              output, provider, lambda *_: None)
    assert not provider.calls


async def test_reference_selection_cannot_add_an_unselected_main_page(workspace):
    store, output = workspace
    provider = IntegrationProvider([], selections=[[f"{DOC_ID}:1"]])
    with pytest.raises(ValueError, match="位置|引用|范围"):
        await generate_lesson(multiple_request(), multiple_documents(), store, output, provider, lambda *_: None)
    assert not any(name == "LessonPlan" for name, *_ in provider.calls)


async def test_combined_reference_limit_fails_without_dropping_primary(workspace, monkeypatch):
    store, output = workspace
    monkeypatch.setattr(pipeline, "MAX_SELECTED_UNITS", 2)
    provider = IntegrationProvider([], selections=[[f"{REFERENCE_ID}:2"]])
    with pytest.raises(ValueError, match="上限|最多|单元|范围"):
        await generate_lesson(multiple_request(ranges={DOC_ID: "1-2"}), multiple_documents(), store,
                              output, provider, lambda *_: None)
    assert not any(name == "LessonPlan" for name, *_ in provider.calls)
    assert not (output / "plan.json").exists()


async def test_primary_limit_fails_before_any_auxiliary_model_call(workspace, monkeypatch):
    store, output = workspace
    monkeypatch.setattr(pipeline, "MAX_SELECTED_UNITS", 1)
    provider = IntegrationProvider([])
    with pytest.raises(ValueError, match="上限|最多|单元|范围"):
        await generate_lesson(multiple_request(ranges={DOC_ID: "1-2"}), multiple_documents(), store,
                              output, provider, lambda *_: None)
    assert not provider.calls


async def test_a_reference_only_plan_cannot_replace_the_primary_lesson(workspace):
    store, output = workspace
    provider = IntegrationProvider([f"{DOC_ID}:2", f"{REFERENCE_ID}:2"],
                                   selections=[[f"{REFERENCE_ID}:2"]], reference_only_plan=True)
    with pytest.raises(ValueError, match="计划|主资料"):
        await generate_lesson(multiple_request(), multiple_documents(), store, output, provider, lambda *_: None)
    assert [name for name, *_ in provider.calls].count("LessonPlan") == 3
    assert not any(name == "LessonSection" for name, *_ in provider.calls)


@pytest.mark.parametrize("fault", ["omit_notes", "forged_note"])
async def test_multiple_material_explanations_require_real_per_file_notes(workspace, fault):
    store, output = workspace
    provider = IntegrationProvider([f"{DOC_ID}:2", f"{REFERENCE_ID}:2"],
                                   selections=[[f"{REFERENCE_ID}:2"]], **{fault: True})
    with pytest.raises(ExceptionGroup) as caught:
        await generate_lesson(multiple_request(), multiple_documents(), store, output, provider, lambda *_: None)
    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)


async def test_topics_search_all_files_and_keep_each_files_actual_refs(workspace):
    store, output = workspace
    expected = [f"{DOC_ID}:2", f"{REFERENCE_ID}:2"]
    provider = IntegrationProvider(expected, selections=[[expected[0]], [expected[1]]])
    lesson = await generate_lesson(multiple_request(mode="topics", ranges={}), multiple_documents(),
                                   store, output, provider, lambda *_: None)
    assert [source.ref for source in lesson.sources] == expected
    assert all(source.role == "topic" for source in lesson.sources)
    assert not any(name == "StudyFocus" for name, *_ in provider.calls)
    searches = [user for name, user, _ in provider.calls if name == "TopicSelection"]
    assert len(searches) == 2
    assert "主课件.md" in searches[0] and "辅助教材.md" in searches[1]
    reasoning = json.loads((output / "scope-reasoning.json").read_text(encoding="utf-8"))
    assert all(ref in json.dumps(reasoning) for ref in expected)


async def test_topic_batch_cannot_claim_a_different_files_ref(workspace):
    store, output = workspace
    provider = IntegrationProvider([], selections=[[f"{REFERENCE_ID}:2"]])
    with pytest.raises(ValueError, match="位置|引用|范围"):
        await generate_lesson(multiple_request(mode="topics", ranges={}), multiple_documents(),
                              store, output, provider, lambda *_: None)
    assert not any(name == "LessonPlan" for name, *_ in provider.calls)


async def test_review_receives_actual_material_images_and_final_output_uses_revision(workspace):
    store, output = workspace
    folder = store.directory("documents", DOC_ID)
    folder.mkdir()
    image_path = folder / "page.png"
    image_path.write_bytes(b"local page fixture; no real provider involved")
    source = document(images=True)
    revised_text = "审校修订：条件概率的分母对应已知事件的概率，需要严格保留分母为正的前提。" * 2

    def revise(draft):
        draft["explanation"] = revised_text
        draft["study_prompts"][0].update(kind="question", task="条件概率为何要求分母为正？",
                                          answer="该定义要除以已知事件的概率，因此分母不能为零。")
        return draft

    provider = SequenceProvider([plan(), section(1), section(2)], revise=revise)
    parameters = GenerateRequest(document_ids=[DOC_ID], section_count=2, api=APIConfig(vision=True))
    lesson = await generate_lesson(parameters, [source], store, output, provider, lambda *_: None)
    assert len(provider.reviews) == 2
    for draft, prompt, pictures in provider.reviews:
        assert draft["worked_example"] in prompt
        assert source.units[0].text in prompt
        assert all(ref in prompt for ref in draft["source_refs"])
        assert pictures == [image_path]
    assert all(item.explanation == revised_text for item in lesson.sections)
    assert all(item.study_prompts[0].kind == "question" and item.study_prompts[0].answer for item in lesson.sections)
    saved = json.loads((output / "section-1.json").read_text(encoding="utf-8"))
    assert saved["explanation"] == revised_text
    assert saved["study_prompts"][0]["answer"]


@pytest.mark.parametrize("field,value", [("id", "wrong-id"), ("source_refs", ["invented:99"])])
async def test_review_cannot_change_approved_section_identity_or_reference(workspace, field, value):
    store, output = workspace

    def revise(draft):
        draft[field] = value
        return draft

    provider = SequenceProvider([plan(), section(1), section(2)], revise=revise)
    with pytest.raises(ExceptionGroup) as caught:
        await generate_lesson(request(), [document()], store, output, provider, lambda *_: None)
    assert any(isinstance(error, ValueError) and "计划" in str(error) for error in caught.value.exceptions)
    assert not (output / "section-1.json").exists()
