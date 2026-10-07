"""An authored example for trying the real PDF renderer without an API call."""
from .models import Concept, Lesson, LessonSection, Overview, Pause, Practice, SourceCitation, StudyPrompt


def demo_lesson() -> Lesson:
    return Lesson(
        title="条件概率与独立性", subtitle="内置示例 · 无需 API · 非模型生成",
        scope_note="LearnMargin 自编示例：条件概率定义、反向条件与独立性。",
        overview=Overview(
            summary="条件概率先确定已知条件下的参照范围，独立性再比较知道条件前后的概率。两者通过交集概率相连。",
            concepts=[
                Concept(name="条件概率", explanation="$P(A|B)=P(A\\cap B)/P(B)$，要求 $P(B)>0$。",
                        connections="分母由竖线右侧的已知条件决定。"),
                Concept(name="独立性", explanation="$P(A\\cap B)=P(A)P(B)$。",
                        connections="当 $P(B)>0$ 时，等价于 $P(A|B)=P(A)$。"),
            ],
            learning_path=["先用人数表理解分母。", "再比较交集与乘积，判断独立性。"],
            prerequisites=["已理解概率是某事件发生的可能性。"],
        ),
        sections=[
            LessonSection(id="s1", title="条件概率：从谁里面选", source_refs=["example:1"],
                explanation="已知 $B$ 发生后，只在 $B$ 的范围内考察 $A$。\n\n"
                    "$$P(A|B)=\\frac{P(A\\cap B)}{P(B)},\\qquad P(B)>0$$\n\n"
                    "竖线右侧是已知条件。$P(A|B)$ 与 $P(B|A)$ 的参照范围不同，通常不能交换。"
                    "若 $P(B)=0$，这个公式没有定义，不能把 $0/0$ 写成零。",
                worked_example="**完整例题**：班级30人，社团成员 $A$ 有12人，戴眼镜者 $B$ 有10人，两者都是的有6人。随机等可能抽一人。\n\n"
                    "| 所求 | 计算 | 参照范围 |\n|---|---|---|\n"
                    "| $P(A)$ | $12/30=2/5$ | 全班30人 |\n"
                    "| $P(A\\cap B)$ | $6/30=1/5$ | 全班30人 |\n"
                    "| $P(A\\mid B)$ | $6/10=3/5$ | 戴眼镜的10人 |\n"
                    "| $P(B\\mid A)$ | $6/12=1/2$ | 社团的12人 |\n\n"
                    "同样是6名同时满足条件的学生，换了已知条件，分母也随之改变。",
                study_prompts=[StudyPrompt(id="s1-a1", kind="question", placement="after_example", when="看完例题后", task="遮住表格，解释为什么两个条件概率的分母分别是10和12。", check="核对竖线右侧代表的人群。",
                    answer="$P(A|B)$ 已知戴眼镜，所以在10人中选；$P(B|A)$ 已知参加社团，所以在12人中选。"),
                    StudyPrompt(id="s1-a2", kind="action", placement="before_example", when="初次看例题",
                    task="第一遍可以对照完整解答。沿着题干找到每个分母对应的人群；遇到不明白的转折，先标记这一处再回查定义。",
                    check="看懂后再遮住表格尝试回想，不必一开始就凭空猜解法。")],
                pause=Pause(when="若本轮已专注约25分钟，休息5分钟；计时先到可记下卡点先停。",
                            activity="放下材料，起身活动。", resume="回来先说出条件概率的分母由谁决定。")),
            LessonSection(id="s2", title="独立性：比较概率关系", source_refs=["example:2"],
                explanation="两个事件独立的定义是交集概率等于边际概率的乘积：\n\n"
                    "$$P(A\\cap B)=P(A)P(B).$$\n\n"
                    "若 $P(B)>0$，两边除以 $P(B)$，得到 $P(A|B)=P(A)$：知道 $B$ 没有改变 $A$ 的概率。"
                    "独立不等于互斥。概率均正的互斥事件交集概率为0，而两个正概率的乘积大于0，所以不独立。",
                worked_example="**接上例**：$P(A\\cap B)=1/5$，而 $P(A)P(B)=(2/5)(1/3)=2/15$。\n\n"
                    "两者不同，所以社团成员与戴眼镜在这个抽样模型中不独立。"
                    "也可以比较 $P(A|B)=3/5$ 与 $P(A)=2/5$，得到同样结论。这个关系不说明因果。",
                practice=[Practice(id="s2-q1", prompt="40张卡中红卡16张、星号卡10张，同时满足的4张。等可能抽一张，求 $P(R|S)$ 并判断独立性。",
                    hint="先确定星号卡的总数，再比较交集与乘积。",
                    answer="$P(R|S)=4/10=2/5$。$P(R\\cap S)=4/40=1/10$，"
                           "$P(R)P(S)=(16/40)(10/40)=1/10$，所以独立。")],
                study_prompts=[StudyPrompt(id="s2-a1", kind="action", placement="before_practice", when="做卡片题前",
                    task="先留下自己的尝试，再看提示或答案。记下本题是独立完成、用过提示，还是仍有卡点；卡住时保留第一处不确定的步骤。",
                    check="本题下方有答案入口。核对后针对第一处差异回查解释，再闭卷重做。")]),
        ],
        review_plan=["明天只看班级题干，重建两个条件概率。", "隔两天重做卡片题，说明独立性的判断依据。"],
        method_chapters=[4, 6, 7, 14],
        sources=[SourceCitation(ref="example:1", document="LearnMargin自编示例", label="条件概率"),
                 SourceCitation(ref="example:2", document="LearnMargin自编示例", label="独立性")],
    )
