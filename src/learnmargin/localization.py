"""Lesson-owned display text, generated with the plan in the requested language."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


def label(description: str):
    return Field(min_length=1, max_length=160, description=description)


def note(description: str):
    return Field(min_length=1, max_length=800, description=description)


class LessonText(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    language_tag: str = Field(pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
                              description="所选语言的 BCP 47 标签，如 zh-CN、en、ja；未知则 und。")
    document: str = label("讲义阅读区域名称：学习讲义")
    overview: str = label("总览页标题：内容总览")
    global_view: str = label("总览摘要标题：先理解全局")
    concept_relations: str = label("概念联系标题：概念关系")
    learning_path: str = label("学习顺序标题：学习路线")
    study_rhythm: str = label("学习节奏提示标题：学习节奏")
    rhythm_note: str = note("简短提示：可试25分钟专注、5分钟休息；提示位置只是建议，计时先到或累了可记下位置先停，时长可调整。")
    prerequisites: str = label("前置知识标题：需要的基础")
    contents: str = label("正文目录标题：正文导航")
    answers: str = label("后置答案页标题：参考解答")
    review_sources: str = label("末尾复习与来源页标题：复习与来源")
    section: str = label("章节编号前的名称：章节")
    material: str = label("材料引用前的名称：材料")
    view_source: str = label("打开原材料按钮：查看原材料")
    source_comparison: str = label("对照不同资料的标题：资料对照")
    worked_example: str = label("完整例题标题：完整例题 · 理解每一步")
    practice: str = label("练习编号前的名称：独立练习")
    hint: str = label("练习提示标题：卡住时看这条提示")
    try_then_answer: str = label("练习跳转答案链接：尝试后前往参考解答；不含箭头")
    view_answer: str = label("侧栏跳转答案链接：查看参考答案；不含箭头")
    pause: str = label("休息提示标题：休息点")
    minutes: str = label("分钟单位：分钟")
    after_pause: str = label("休息后继续提示前的名称：回来后")
    answer_note: str = note("一句核对提示：先独立尝试，再核对第一处差异；概念不清楚时回看讲解后重做。")
    return_question: str = label("返回练习链接：返回题目；不含箭头")
    sidebar: str = label("侧栏答案编号前的名称：侧栏")
    return_prompt: str = label("返回侧栏链接：返回这条提示；不含箭头")
    review_step: str = label("复习步骤编号前的名称：复习安排")
    method_basis: str = label("学习方法来源标题：学习方法依据")
    method_note: str = note("一句说明：参考《学习之道》的方法摘要，例题提示结合材料组织，节奏可按试读反馈调整；不列章节号。")
    method_chapters: str = label("学习之道章节列表前的名称：参考章节")
    source_primary: str = label("来源角色：主材料")
    source_reference: str = label("来源角色：参考资料")
    source_topic: str = label("来源角色：主题相关资料")
    material_notes: str = label("材料与范围说明标题：材料与范围说明")
    return_overview: str = label("返回总览链接：返回内容总览；不含箭头")
    image: str = label("无法直接嵌入图片时，图片说明前的名称：图示")
    scope_all: str = label("学习范围标签：全部导入内容")
    scope_primary: str = label("学习范围中的主材料数量标签：主材料单元")
    scope_reference: str = label("学习范围中的参考材料数量标签：参考资料单元")
    scope_topic: str = label("学习范围中的用户知识点标签：知识点")
    scope_units: str = label("学习范围中的总数量标签：材料单元")
    scope_location_note: str = note("范围说明：位置按文件页码、幻灯片或章节编号，不等同于印刷页码。")
    pause_when: str = note("就地休息条件：若距上次休息已专注约25分钟，休息5分钟；时间未到可继续。")
    pause_activity: str = note("兜底休息动作：放下讲义，起身走动或喝水。")
    pause_resume: str = note("就地返回动作：先回想刚读过的核心关系，再从标记处继续。")


def chinese_lesson_text() -> LessonText:
    """Compatibility default for authored demos and previously saved lessons."""
    return LessonText(
        language_tag="zh-CN", document="学习讲义", overview="内容总览", global_view="先理解全局",
        concept_relations="概念关系", learning_path="学习路线", study_rhythm="学习节奏",
        rhythm_note="可先按 25 分钟专注、5 分钟休息计时。提示位置只是建议；计时先到或累了，记下位置即可停下，不必等到休息点。",
        prerequisites="需要的基础", contents="正文导航", answers="参考解答", review_sources="复习与来源",
        section="章节", material="材料", view_source="查看原材料", source_comparison="资料对照",
        worked_example="完整例题 · 理解每一步", practice="独立练习", hint="卡住时看这条提示",
        try_then_answer="尝试后前往参考解答", view_answer="查看参考答案", pause="休息点", minutes="分钟",
        after_pause="回来后", answer_note="先独立尝试，再核对第一处差异。若概念仍不清楚，回看对应讲解后重新做一遍。",
        return_question="返回题目", sidebar="侧栏", return_prompt="返回这条提示", review_step="复习安排",
        method_basis="学习方法依据", method_note="参考《学习之道》的方法摘要；配套例题与提示由本讲义结合材料组织。节奏可按试读反馈调整。",
        method_chapters="参考章节", source_primary="主材料", source_reference="参考资料", source_topic="主题相关资料",
        material_notes="材料与范围说明", return_overview="返回内容总览", image="图示",
        scope_all="全部导入内容", scope_primary="主材料单元", scope_reference="参考资料单元",
        scope_topic="知识点", scope_units="材料单元",
        scope_location_note="位置按文件页码、幻灯片或章节编号，不等同于印刷页码。",
        pause_when="若距上次休息已专注约25分钟，休息5分钟；时间未到可继续。",
        pause_activity="放下讲义，起身走动或喝水。", pause_resume="先回想刚读过的核心关系，再从标记处继续。",
    )
