from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from learnmargin.demo import demo_lesson
from learnmargin.models import GeneratedLessonSection, LessonSection, Pause, StudyLoad
from learnmargin.study_rhythm import plan_pauses


def section(explanation=5, example=5, practice=0, *, legacy=False, explicit_pause=False):
    data = {
        "id": "section", "title": "A complete concept", "source_refs": ["D1:1"],
        "explanation": "A complete proof with its assumptions and intermediate steps. " * 2,
        "worked_example": "A complete worked example with all necessary checks.",
        "practice": [{"id": f"q-{n}", "prompt": "Apply the concept.", "hint": "Check assumptions.",
                      "answer": "A full answer."} for n in range(1, 3)] if practice else [],
    }
    if not legacy:
        data["study_load"] = {"explanation_minutes": explanation, "worked_example_minutes": example,
                              "practice_minutes": practice,
                              "rationale": "Understand a derivation, follow the example, then solve and check the tasks."}
    if explicit_pause:
        data["pause"] = Pause(when="If your timer has reached about 25 minutes, take a break.",
                              activity="Stand up.", resume="Recall the completed idea.")
    return LessonSection.model_validate(data)


def lesson_with(*sections):
    lesson = demo_lesson()
    lesson.sections = list(sections)
    return lesson


def test_two_short_exercises_share_one_break_after_the_complete_group():
    result = plan_pauses(lesson_with(section(17, 5, 2)))
    assert len(result) == 1
    assert result[0]["boundary"] == "practice-2"
    assert result[0]["estimated_minutes_since_break"] == 24
    assert result[0]["kind"] == "end"


def test_short_sections_accumulate_across_chapter_boundaries():
    lesson = lesson_with(section(), section(), section())
    result = plan_pauses(lesson)
    assert [(item["section"], item["boundary"]) for item in result] == [(3, "explanation")]
    assert result[0]["estimated_minutes_since_break"] == 25
    assert result[0]["basis"] == "content_estimate"
    assert result[0]["rationale"] == lesson.sections[0].study_load.rationale


def test_long_indivisible_proof_is_not_split_to_match_target():
    result = plan_pauses(lesson_with(section(50, 5)))
    assert len(result) == 1
    assert result[0]["boundary"] == "explanation"
    assert result[0]["estimated_minutes_since_break"] == 50


def test_internal_nineteen_minute_boundary_is_better_than_next_thirty_five_minute_boundary():
    result = plan_pauses(lesson_with(section(19, 16)))
    assert len(result) == 1
    assert result[0]["boundary"] == "explanation"
    assert result[0]["estimated_minutes_since_break"] == 19


def test_tiny_task_before_a_long_indivisible_block_does_not_trigger_early_break():
    result = plan_pauses(lesson_with(section(3, 45)))
    assert len(result) == 1
    assert result[0]["boundary"] == "example"
    assert result[0]["estimated_minutes_since_break"] == 48


def test_eighteen_minute_final_remainder_does_not_get_a_forced_break():
    assert plan_pauses(lesson_with(section(10, 8))) == []


@pytest.mark.parametrize("example,expected_boundary,expected_minutes", [
    (14, "explanation", 20), (6, "example", 26), (10, "explanation", 20),
])
def test_choose_nearest_complete_boundary_and_do_not_add_short_tail(example, expected_boundary, expected_minutes):
    result = plan_pauses(lesson_with(section(20, example)))
    assert len(result) == 1
    assert result[0]["boundary"] == expected_boundary
    assert result[0]["estimated_minutes_since_break"] == expected_minutes


def test_short_new_lesson_does_not_force_author_pause_or_claim_elapsed_time():
    lesson = lesson_with(section(4, 3, explicit_pause=True))
    before = lesson.model_dump()
    assert plan_pauses(lesson) == []
    assert lesson.model_dump() == before


def test_repeated_breaks_each_have_substantial_intervening_work():
    result = plan_pauses(lesson_with(section(25, 2, 2), section(10, 10, 5), section(5, 3)))
    assert [(item["section"], item["boundary"]) for item in result] == [(1, "explanation"), (2, "example")]
    assert [item["estimated_minutes_since_break"] for item in result] == [25, 24]


@pytest.mark.parametrize("mixed", [False, True])
def test_legacy_or_mixed_lessons_preserve_only_explicit_author_breaks(mixed):
    lesson = lesson_with(section(legacy=True), section(legacy=True, explicit_pause=True),
                         section(60, 20, legacy=not mixed))
    result = plan_pauses(lesson)
    assert result == [{"section": 2, "boundary": "example", "kind": "end",
                       "estimated_minutes_since_break": None, "basis": "legacy_author", "rationale": ""}]


def test_new_generation_requires_estimates_but_saved_legacy_lessons_still_load():
    legacy_data = section(legacy=True).model_dump(exclude_none=True)
    assert LessonSection.model_validate(legacy_data).study_load is None
    with pytest.raises(ValidationError) as error:
        GeneratedLessonSection.model_validate(legacy_data)
    assert error.value.errors()[0]["loc"] == ("study_load",)
    assert GeneratedLessonSection.model_validate(section().model_dump()).study_load is not None


@pytest.mark.parametrize("field,value", [
    ("explanation_minutes", 0), ("worked_example_minutes", -1), ("practice_minutes", -1),
    ("explanation_minutes", math.inf), ("worked_example_minutes", math.nan), ("rationale", "   "),
])
def test_invalid_estimates_cannot_silently_create_a_schedule(field, value):
    data = section().study_load.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        StudyLoad.model_validate(data)


@pytest.mark.parametrize("has_practice,minutes", [(True, 0), (False, 4)])
def test_practice_load_must_match_actual_tasks(has_practice, minutes):
    data = section(practice=5 if has_practice else 0).model_dump()
    data["study_load"]["practice_minutes"] = minutes
    with pytest.raises(ValidationError):
        GeneratedLessonSection.model_validate(data)
