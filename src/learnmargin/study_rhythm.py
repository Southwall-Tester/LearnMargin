"""Select sparse break opportunities from estimated work, never from pagination.

The estimates guide placement only. A PDF cannot measure elapsed study time;
the displayed condition still defers to the learner's timer and fatigue.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from .models import Lesson, LessonSection

TARGET_MINUTES = 25.0
MINIMUM_INTERNAL_MINUTES = 15.0
MINIMUM_TAIL_MINUTES = 20.0


class PausePlacement(TypedDict):
    section: int
    boundary: str
    kind: Literal["middle", "end"]
    estimated_minutes_since_break: float | None
    basis: Literal["content_estimate", "legacy_author"]
    rationale: str


def _last_boundary(section: LessonSection) -> str:
    return f"practice-{len(section.practice)}" if section.practice else "example"


def plan_pauses(lesson: Lesson) -> list[PausePlacement]:
    """Accumulate complete explanation/example/practice groups across sections.

    Choose the complete boundary nearest 25 minutes, considering both sides
    of the target. An internal candidate needs at least 15 estimated minutes
    so a tiny task before a long proof does not trigger a premature break.
    If the final remainder never reaches 25, require at least 20 minutes.
    These are product scheduling heuristics, not measured time or scientifically
    established thresholds. Do not split a proof or exercise group to meet them.

    A mixed legacy/new lesson has an unknown total load. Preserve only explicit
    author pauses in that case; do not fabricate estimates or automatic stops.
    """
    if any(section.study_load is None for section in lesson.sections):
        return [PausePlacement(
            section=index, boundary=_last_boundary(section), kind="end",
            estimated_minutes_since_break=None, basis="legacy_author", rationale="",
        ) for index, section in enumerate(lesson.sections, 1) if section.pause is not None]

    # Each block is indivisible. In particular both short exercises share a
    # single final boundary, so adjacent question cards cannot create breaks.
    blocks: list[tuple[int, str, float, str]] = []
    for index, section in enumerate(lesson.sections, 1):
        load = section.study_load
        assert load is not None
        blocks.extend([
            (index, "explanation", load.explanation_minutes, load.rationale),
            (index, "example", load.worked_example_minutes, load.rationale),
        ])
        if section.practice:
            blocks.append((index, _last_boundary(section), load.practice_minutes, load.rationale))

    placements: list[PausePlacement] = []
    start = 0
    while start < len(blocks):
        total = 0.0
        eligible: list[tuple[float, int, float]] = []
        for end in range(start, len(blocks)):
            total += blocks[end][2]
            if total >= MINIMUM_INTERNAL_MINUTES:
                eligible.append((abs(total - TARGET_MINUTES), end, total))
            if total >= TARGET_MINUTES:
                break
        if total < TARGET_MINUTES:
            eligible = [candidate for candidate in eligible if candidate[2] >= MINIMUM_TAIL_MINUTES]
        if not eligible:
            break
        # An equal distance prefers the earlier complete boundary.
        _, chosen, estimated = min(eligible)
        section_index, boundary, _, _ = blocks[chosen]
        rationales = list(dict.fromkeys(block[3] for block in blocks[start:chosen + 1]))
        placements.append(PausePlacement(
            section=section_index,
            boundary=boundary,
            kind="end" if boundary == _last_boundary(lesson.sections[section_index - 1]) else "middle",
            estimated_minutes_since_break=round(estimated, 2),
            basis="content_estimate",
            rationale="\n".join(rationales),
        ))
        start = chosen + 1
    return placements
