# 在 AI 会话中使用 LearnMargin Skill

这是独立的讲义生成技能包，适合直接使用已有 AI 会话制作学习 PDF。无需安装 LearnMargin 软件、购买 API 额度或填写 API Key。LearnMargin 软件是另一个可选产品。

1. 将整个 `learnmargin-skill.zip` 提供给能读取文件的 AI 会话，或解压后让 AI 读取其中的 `learnmargin/SKILL.md`。请保留 `references/`、`assets/` 和 `scripts/`，只复制入口会缺少完整的方法资料。
2. 提供学习材料及范围，例如“这份 PDF 的文件第 50～60 页，另一份讲义作为辅助”，或“综合这些文件讲清条件概率”。需要时补充学习基础和具体困难。
3. 让当前 AI 按技能读取材料、选用内置方法并直接生成 PDF；试读后继续反馈，它会调整讲义与可复用规则。

可直接这样说明：

> 请读取附件技能包的 SKILL.md 和需要的 references，直接按其中方法处理我的材料。学习范围是……。用你当前可用的文档工具生成带总览、A4 内侧栏和后置参考答案的 PDF，不要求我配置 API 或安装软件。

是否支持原生安装 skills、读取 ZIP、代码执行和生成 PDF 附件，取决于所用客户端及当前会话能力；本包不假定聊天客户端具有原生 skills 功能。没有原生安装入口时，可把包内说明与参考作为本次任务材料提供。若会话无法生成文件，应说明缺少的能力，保留已编写的内容或可导出的 HTML，不能把未生成的 PDF 当作已交付。

## 可选脚本

当前 AI 可以使用宿主已有的文档、PDF 或代码工具，不必运行包内脚本。若选择本地辅助脚本：

- `scripts/render_html.py`：将已编写的 HTML 导出 PDF，并检查分页、提取文字。
- `scripts/add_sidebars.py`：保留源 PDF 并外加侧栏及总览；这是可选宽页流程，不是默认 A4 内栏。

这两个脚本需要 Python、PyMuPDF、Playwright 和可用的 Chromium；字体需覆盖正文语言及数学符号。具体输入与限制见 [references/rendering.md](references/rendering.md)。缺少这些工具时可使用其他排版方式，整个技能不依赖 LearnMargin 应用仓库。

包内包括覆盖《学习之道》18 章的方法摘要，不含整本原书。精确引文需查原书。技能包授权见 [LICENSE](LICENSE)。
