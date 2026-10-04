"""Shared contracts for ingestion, API generation and PDF rendering."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceUnit(Model):
    index: int = Field(ge=1)
    label: str
    text: str
    image_paths: list[str] = Field(default_factory=list)


class Document(Model):
    id: str
    name: str
    kind: str
    unit_label: str
    units: list[SourceUnit]
    warnings: list[str] = Field(default_factory=list)


class APIConfig(Model):
    protocol: Literal["responses", "chat_completions"] = "chat_completions"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    vision: bool = True
    json_mode: bool = True
    timeout_seconds: int = Field(default=180, ge=10, le=600)


class Scope(Model):
    mode: Literal["all", "pages", "topics"] = "all"
    ranges: dict[str, str] = Field(default_factory=dict)
    topics: str = Field(default="", max_length=2000)


class GenerateRequest(Model):
    document_ids: list[str] = Field(min_length=1, max_length=8)
    scope: Scope = Field(default_factory=Scope)
    api: APIConfig = Field(default_factory=APIConfig)
    learner_notes: str = Field(default="", max_length=2000)
    language: str = Field(default="简体中文", max_length=80)
    section_count: int = Field(default=4, ge=2, le=8)
    layout: Literal["a4", "wide"] = "a4"
    reading_mode: Literal["auto", "handwritten"] = "auto"


class Concept(Model):
    name: str
    explanation: str
    connections: str


class Overview(Model):
    summary: str
    concepts: list[Concept] = Field(min_length=1, max_length=10)
    learning_path: list[str] = Field(min_length=1, max_length=10)
    prerequisites: list[str] = Field(default_factory=list)


class PlannedSection(Model):
    id: str
    title: str
    objective: str
    source_refs: list[str] = Field(min_length=1)


class LessonPlan(Model):
    title: str
    subtitle: str
    overview: Overview
    sections: list[PlannedSection] = Field(min_length=2, max_length=8)
    review_plan: list[str] = Field(min_length=1, max_length=8)
    method_chapters: list[int] = Field(min_length=1, max_length=8)


class StudyPrompt(Model):
    id: str
    kind: Literal["question", "action"] = Field(description="需要作答、解释或计算用question并提供answer；纯操作提示用action。")
    when: str
    task: str
    check: str
    answer: str | None = None

    @model_validator(mode="after")
    def question_has_answer(self):
        if self.kind == "question" and (self.answer is None or not self.answer.strip()):
            raise ValueError("需要作答的侧栏提示必须提供参考答案。")
        return self


class Practice(Model):
    id: str
    prompt: str
    hint: str
    answer: str


class Pause(Model):
    minutes: int = Field(default=5, ge=1, le=30)
    when: str
    activity: str
    resume: str


class SourceNote(Model):
    ref: str
    explanation: str = Field(min_length=1, description="这份材料如何解释该知识点，忠实转述，不编引文。")
    relation: str = Field(min_length=1, description="与主材料或其他资料的互补、条件或差异；一致时如实说明。")


class LessonSection(Model):
    id: str
    title: str
    source_refs: list[str] = Field(min_length=1)
    source_notes: list[SourceNote] = Field(default_factory=list)
    explanation: str = Field(min_length=40)
    worked_example: str = Field(min_length=20)
    practice: list[Practice] = Field(default_factory=list, max_length=2)
    study_prompts: list[StudyPrompt] = Field(default_factory=list, max_length=2)
    pause: Pause | None = None


class SourceCitation(Model):
    ref: str
    document: str
    label: str
    role: Literal["primary", "reference", "topic"] = "primary"


class Lesson(Model):
    title: str
    subtitle: str
    overview: Overview
    sections: list[LessonSection]
    review_plan: list[str]
    method_chapters: list[int]
    sources: list[SourceCitation]
    scope_note: str
    warnings: list[str] = Field(default_factory=list)
