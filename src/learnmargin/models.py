"""Shared contracts for ingestion, API generation and PDF rendering."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .localization import LessonText, chinese_lesson_text


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


class ConnectionTestResult(Model):
    ok: Literal[True] = True
    message: Literal["连接成功"] = "连接成功"
    model: str
    latency_ms: int = Field(ge=0)


class Scope(Model):
    mode: Literal["all", "pages", "topics"] = "all"
    ranges: dict[str, str] = Field(default_factory=dict)
    topics: str = Field(default="", max_length=2000)
    include_prerequisites: bool = Field(default=True,
        description="页码模式允许检索同文件范围外的必要定义、前提和补充解释，作为参考而非新增学习范围。")


class GenerateRequest(Model):
    document_ids: list[str] = Field(min_length=1, max_length=8)
    scope: Scope = Field(default_factory=Scope)
    api: APIConfig = Field(default_factory=APIConfig)
    learner_notes: str = Field(default="", max_length=2000)
    language: str = Field(default="简体中文", min_length=1, max_length=80)
    layout: Literal["a4", "wide"] = "a4"
    reading_mode: Literal["auto", "handwritten"] = "auto"

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_section_count(cls, value):
        """Old clients may send this control; it no longer constrains planning."""
        if isinstance(value, dict) and "section_count" in value:
            return {key: item for key, item in value.items() if key != "section_count"}
        return value

    @field_validator("language")
    @classmethod
    def language_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写输出语言。")
        return value.strip()


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
    guidance_focus: str = Field(default="", max_length=600,
        description="本节在哪个学习节点需要什么帮助、为什么适合；可选看示范、独立尝试、反馈纠错、"
        "提取、卡点处理、迁移或复习等，不是题目清单。结合前后节避免重复；无需指导可留空。")


class LessonPlan(Model):
    title: str
    subtitle: str
    text: LessonText
    overview: Overview
    sections: list[PlannedSection] = Field(min_length=1)
    review_plan: list[str] = Field(min_length=1, max_length=8)
    method_chapters: list[int] = Field(min_length=1, max_length=18)


PromptPlacement = Literal["before_explanation", "after_explanation", "before_example", "after_example",
                          "before_practice", "after_practice"]


class StudyPrompt(Model):
    id: str
    kind: Literal["question", "action"] = Field(
        description="要求写出、解释、判断、计算或重建知识答案用question并提供answer；"
        "组织学习过程用action，例如如何读示范、先尝试后看提示、记录卡点、核对后安排重做；"
        "不为流程记录另造标准答案，也不能将新知识题伪装为action以省略答案。"
    )
    placement: PromptPlacement | None = Field(default=None,
        description="卡片实际贴近的内容位置，新生成时明确选择。练习前后指本节整组practice，"
        "没有练习不能选这两个位置；旧数据不填时保留首卡讲解旁、次卡例题旁。")
    when: str
    task: str
    check: str = Field(description="知识题的核对要点随答案后置；流程指导只写需要的后续操作或核对入口，"
        "可留空，不把行动记录当成知识测验。")
    answer: str | None = None

    @model_validator(mode="after")
    def question_has_answer(self):
        if self.kind == "question" and (self.answer is None or not self.answer.strip()):
            raise PydanticCustomError("question_answer_required", "需要作答的侧栏提示必须提供参考答案。")
        return self


class GeneratedStudyPrompt(StudyPrompt):
    placement: PromptPlacement = Field(description="卡片实际位置，与when及当前学习动作一致。"
        "before/after_practice针对本节整组练习，没有练习时不可选。")

    @model_validator(mode="after")
    def action_has_no_knowledge_answer(self):
        if self.kind == "action" and self.answer is not None:
            raise PydanticCustomError("action_answer_not_allowed", "流程指导不另设知识答案；新知识问题请用question。")
        return self


class Practice(Model):
    id: str
    prompt: str
    hint: str
    answer: str


class Pause(Model):
    minutes: int = Field(default=5, ge=1, le=30)
    when: str = Field(description="用所选输出语言表达：若距上次休息已专注约25分钟，休息5分钟；时间未到可继续。")
    activity: str
    resume: str


class SourceNote(Model):
    ref: str
    explanation: str = Field(min_length=1, description="这份材料如何解释该知识点，忠实转述，不编引文。")
    relation: str = Field(min_length=1, description="与主材料或其他资料的互补、条件或差异；一致时如实说明。")


class StudyLoad(Model):
    explanation_minutes: float = Field(gt=0, le=180, allow_inf_nan=False,
        description="理解explanation的概念和完整推导，加上before_explanation/after_explanation提示的合计估计分钟数；"
        "不含source_notes，不按页数或字数换算。")
    worked_example_minutes: float = Field(gt=0, le=180, allow_inf_nan=False,
        description="理解source_notes、跟随worked_example并核对，加上before_example/after_example提示的"
        "合计估计分钟数；这些内容排在explanation休息边界之后，只计入本字段。")
    practice_minutes: float = Field(ge=0, le=180, allow_inf_nan=False,
        description="完成本节全部练习并核对答案，含before_practice/after_practice提示的合计估计分钟数；"
        "没有练习时为0。流程中将来才做的复习不算当前时间；旧提示未指定位置时首条计讲解、次条计例题。")
    rationale: str = Field(min_length=1, max_length=400,
        description="用所选输出语言简述难度、推导步骤、作答及核对所需负荷的依据；不是实际学习记录。")

    @field_validator("rationale")
    @classmethod
    def meaningful_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("学习负荷估计必须说明依据。")
        return value.strip()


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
    study_load: StudyLoad | None = None
    unresolved_prerequisites: list[str] = Field(default_factory=list, max_length=8,
        description="仍缺乏依据、导致无法理解本节或验证论证的必要定义或结论；没有则为空数组。"
        "定义和角色已清楚、仅不知英文全称不算实质缺口。")

    @model_validator(mode="after")
    def practice_load_matches_content(self):
        if not self.practice and any(prompt.placement in {"before_practice", "after_practice"}
                                     for prompt in self.study_prompts):
            raise PydanticCustomError("guidance_placement_without_practice", "本节没有练习，不能把侧栏放在练习前或练习后。")
        if self.study_load is not None and bool(self.practice) != (self.study_load.practice_minutes > 0):
            raise ValueError("有练习时须估计作答和核对负荷；没有练习时practice_minutes必须为0。")
        return self


class GeneratedLessonSection(LessonSection):
    """New generation requires explicit author checks; saved lessons remain compatible."""

    study_load: StudyLoad
    study_prompts: list[GeneratedStudyPrompt] = Field(default_factory=list, max_length=2)
    unresolved_prerequisites: list[str] = Field(max_length=8,
        description="仍缺乏依据、导致无法理解本节或验证论证的必要定义或结论；没有则必须返回空数组。"
        "定义和角色已清楚、仅不知英文全称不算实质缺口。")


class SourceCitation(Model):
    ref: str
    document: str
    label: str
    role: Literal["primary", "reference", "topic"] = "primary"


class Lesson(Model):
    title: str
    subtitle: str
    language: str = "简体中文"
    text: LessonText = Field(default_factory=chinese_lesson_text)
    overview: Overview
    sections: list[LessonSection]
    review_plan: list[str]
    method_chapters: list[int]
    sources: list[SourceCitation]
    scope_note: str
    warnings: list[str] = Field(default_factory=list)
