from __future__ import annotations

import json

import httpx
import pytest

from learnmargin.demo import demo_lesson
from learnmargin.localization import chinese_lesson_text
from learnmargin.models import (
    APIConfig,
    Document,
    GenerateRequest,
    LessonPlan,
    LessonSection,
    Model,
    SourceUnit,
)
from learnmargin.pipeline import generate_lesson
from learnmargin.provider import Provider, ProviderError
from learnmargin.storage import Store


class Reply(Model):
    answer: str


def envelope(protocol, text):
    if protocol == "responses":
        return {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}]}]}
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def user_text(payload, protocol):
    if protocol == "responses":
        return payload["input"][0]["content"][0]["text"]
    return payload["messages"][1]["content"]


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_json_syntax_repair_reports_location_without_replaying_bad_response(protocol):
    requests = []
    malformed = '{\n"answer": "private-response-value\\q"\n}'

    def respond(request):
        requests.append(json.loads(request.content))
        text = malformed if len(requests) == 1 else '{"answer":"repaired"}'
        return httpx.Response(200, json=envelope(protocol, text))

    config = APIConfig(protocol=protocol, base_url="https://model.example/v1", model="test")
    async with Provider(config, transport=httpx.MockTransport(respond)) as provider:
        reply = await provider.generate(Reply, "Test", "Source material")
    assert reply.answer == "repaired"
    assert len(requests) == 2
    repair = user_text(requests[1], protocol)
    assert "invalid_escape" in repair and "第 2 行" in repair and "列" in repair
    assert "完整 JSON 对象" in repair
    assert "private-response-value" not in repair


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_schema_repair_locates_missing_answer_without_weakening_answer_requirement(protocol):
    good = demo_lesson().sections[0].model_dump()
    bad = json.loads(json.dumps(good))
    bad["study_prompts"][0].update(task="private-generated-prompt", answer=None)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        value = bad if len(requests) == 1 else good
        return httpx.Response(200, json=envelope(protocol, json.dumps(value, ensure_ascii=False)))

    config = APIConfig(protocol=protocol, base_url="https://model.example/v1", model="test")
    async with Provider(config, transport=httpx.MockTransport(respond)) as provider:
        section = await provider.generate(LessonSection, "Test", "Source material")
    assert section.study_prompts[0].answer
    assert len(requests) == 2
    repair = user_text(requests[1], protocol)
    assert "$.study_prompts[0]" in repair and "question_answer_required" in repair
    assert "answer" in repair or "参考答案" in repair
    assert "private-generated-prompt" not in repair


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_final_diagnostics_hide_values_and_unknown_field_names_and_remain_bounded(protocol):
    requests = []
    invalid = {"answer": {"private-input-value": "secret"}, "private-extra-field-name": "secret"}

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(protocol, json.dumps(invalid)))

    config = APIConfig(protocol=protocol, base_url="https://model.example/v1", model="test")
    async with Provider(config, transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError) as caught:
            await provider.generate(Reply, "Test", "Source material")
    assert len(requests) == 2
    error = str(caught.value)
    assert "Reply" in error and "已重试 1 次" in error
    assert "$.answer" in error and "string_type" in error and "extra_forbidden" in error
    for text in (error, user_text(requests[1], protocol)):
        assert "private" not in text and "secret" not in text
        assert "[未定义字段]" in text


async def test_non_object_output_keeps_required_object_contract():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=envelope("chat_completions", '["private-value"]'))

    async with Provider(APIConfig(base_url="https://model.example/v1"),
                        transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError, match="object_required") as caught:
            await provider.generate(Reply, "Test", "Source material")
    assert len(requests) == 2
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize("failed_stage", ["初稿", "审校"])
async def test_section_failure_reports_stage_and_preserves_completed_sections(tmp_path, failed_stage):
    document_id = "a" * 32
    lesson = demo_lesson()
    for index, section in enumerate(lesson.sections, 1):
        section.source_refs = [f"{document_id}:{index}"]
    plan = LessonPlan(
        title=lesson.title, subtitle=lesson.subtitle, text=chinese_lesson_text(), overview=lesson.overview,
        sections=[{"id": section.id, "title": section.title, "objective": "理解本节内容",
                   "source_refs": section.source_refs} for section in lesson.sections],
        review_plan=lesson.review_plan, method_chapters=lesson.method_chapters,
    )

    class StageProvider:
        async def generate(self, schema, system, user, images=None):
            if schema is LessonPlan:
                return plan
            if schema.__name__ == "SectionSourceReview":
                return schema.model_validate({"additions": []})
            planned, _ = json.JSONDecoder().raw_decode(user.split("本节计划：", 1)[1])
            stage = "审校" if "待审校初稿JSON：" in user else "初稿"
            if planned["id"] == "s2" and stage == failed_stage:
                raise ProviderError("LessonSection 两次输出均未通过结构校验：$.study_prompts[0]（value_error）。")
            return lesson.sections[int(planned["id"][1:]) - 1].model_copy(deep=True)

    document = Document(id=document_id, name="fixture.txt", kind="txt", unit_label="段", units=[
        SourceUnit(index=index, label=f"第 {index} 段", text="条件概率改变参照范围。" * 5)
        for index in (1, 2)])
    output = tmp_path / "output"
    request = GenerateRequest(document_ids=[document_id], api=APIConfig(vision=False))
    with pytest.raises(ExceptionGroup) as caught:
        await generate_lesson(request, [document], Store(tmp_path / "data"), output,
                              StageProvider(), lambda *_: None)
    error = str(caught.value.exceptions[0])
    assert f"第 2/2 节《{lesson.sections[1].title}》{failed_stage}失败" in error
    assert "$.study_prompts[0]" in error
    assert LessonSection.model_validate_json((output / "section-1.json").read_text(encoding="utf-8"))
    assert not (output / "section-2.json").exists()
    completed = json.loads((output / "section-1-status.json").read_text(encoding="utf-8"))
    failed = json.loads((output / "section-2-status.json").read_text(encoding="utf-8"))
    assert completed["status"] == "completed" and completed["stage"] == "审校"
    assert failed["status"] == "failed" and failed["stage"] == failed_stage
    assert set(failed) == {"section", "total", "stage", "status", "error"}
