# LearnMargin

**Understand the content. Know how to learn.**

[![CI](https://github.com/Southwall-Tester/LearnMargin/actions/workflows/ci.yml/badge.svg)](https://github.com/Southwall-Tester/LearnMargin/actions/workflows/ci.yml)

把选定范围的课件、教材和笔记，变成带内容总览、详细讲解、例题与学习侧栏的 PDF。

LearnMargin 提供两个独立产品，共用按《学习之道》18 章整理的方法参考：

| 产品 | 使用方式 | 是否需要另配 API |
|---|---|---|
| [LearnMargin Skill](skills/learnmargin/USAGE.md) | 在现有 AI 会话中提供技能包、材料与范围，让 AI 直接生成 PDF | 不需要；由当前会话的 AI 和文档工具执行 |
| LearnMargin 软件 | 本地 Web 界面上传资料、生成讲义、来回查看来源 | 需要配置兼容模型 API；内置示例除外 |

只有 AI 订阅、没有 API 的使用者，直接选择 Skill。Skill 不依赖安装软件或启动本地服务；软件也不要求用户先在 AI 客户端安装 Skill。两者可在同一仓库维护，但分别交付 `learnmargin-skill.zip` 与 Python 软件包。

软件包与独立 Skill 包可从 [GitHub Releases](https://github.com/Southwall-Tester/LearnMargin/releases/latest) 下载；版本变化见 [更新记录](CHANGELOG.md)。

## 直接使用 Skill

取得整个 [`skills/learnmargin/`](skills/learnmargin) 文件夹或 `learnmargin-skill.zip`，交给能够读取附件并生成文件的 AI 会话。支持技能目录的客户端也可按其规则安装该文件夹；不支持原生技能安装时，提供整个包并要求读取 `SKILL.md` 和相关参考文件即可。

例如：“按照 LearnMargin Skill，结合这份课本和 PPT，讲解 PPT 第 10～20 页，并参考课本相关内容。生成 A4 内侧栏 PDF，包含知识总览、详细讲解、少量学习提示、后置答案与休息安排。”

技能自带方法库，不要求再提供《学习之道》原书；用户仍需提供要学习的材料。PDF 使用当前 AI 环境已有的文件生成工具制作，可选脚本的依赖见技能使用说明。

## 软件使用流程

1. 导入一份或多份材料，查看解析结果。
2. 指定主材料的文件页码、幻灯片或章节范围，其余材料自动检索为参考；也可填写知识点，跨全部资料检索。
3. 选择模型、输出语言和版式，填写必要的学习需求。输出默认简体中文，也可选择其他语言或自定义，不随材料语言自动切换。
4. 生成 PDF，预览、下载，或在网页阅读中查看原材料并返回原位置；导出 HTML 和 JSON 可继续编辑。

讲义先给实质性的知识总览，再讲清概念、条件与推导。侧栏只保留有帮助的提示，需要作答的提示有后置参考答案与双向跳转。休息点按理解、例题和练习的预计负荷组合安排，短任务可跨章节合并，不按页数或每章一条的方式插入。用时估计及依据保存在生成数据中，实际休息以计时和疲劳为准。

同时提供课本和 PPT 时，讲义按知识点整合，注明每份资料怎么说、具体位置以及互补或差异。页码模式完整保留主材料的指定范围，其他文件仅补充相关内容；未找到相关内容会说明。PDF 保留关键解释，能独立阅读；网页的“查看原材料”打开对应页或章节，“返回讲义”回到原位置。

**A4 标准版**将正文和侧栏放在同一张 A4 内，适合打印与平板阅读；**电脑宽版**提供更多横向空间。两种版式都能在前端选择。

题目与答案双向定位到具体卡片。新导出的 PDF 使用 125% 跳转缩放，避免继承整页适应后只看出翻页；Web 会突出显示目标。独立 Skill 的附栏脚本可调整跳转缩放。

**手写与扫描讲义**：自动模式会对缺少可提取文字的图像页进行多模态转录；“手写讲义”模式会逐个图像单元识读文字与公式，即使页面带有印刷页脚。公式保留为 LaTeX，无法辨认处标记“待核对”。网页来源同时提供原图、识读文本和疑点；原文件不被覆盖。这个步骤需要模型支持图片，并会增加 API 调用。导出包保留 `transcription.json`，便于复核。真实笔迹小样本验证和开源 OCR 调研见[识读说明](docs/OCR.md)。

## 本地启动

需要 Python 3.11+、Node.js 22+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。以下命令适用于 PowerShell、Bash：

```shell
git clone https://github.com/Southwall-Tester/LearnMargin.git
cd LearnMargin
npm --prefix frontend ci
npm --prefix frontend run build
uv sync --frozen --extra dev
uv run playwright install chromium
uv run learnmargin --open
```

打开 **http://127.0.0.1:8765**。先点“体验内置示例”可检查 PDF 引擎；该示例是预先编写的内容，不调用模型。首次安装 Chromium 需要联网，PDF 渲染运行时不依赖 CDN。

Linux 还需中文字体和 Chromium 系统依赖：

```shell
sudo apt-get update
sudo apt-get install -y fonts-noto-cjk
uv run playwright install --with-deps chromium
```

Windows 也可以在完成安装后运行根目录的 `Start-LearnMargin.cmd`。

更新后先关闭旧服务，再重新启动；仅刷新页面不会更新后端。端口被占用时，启动器会提示处理旧服务，不打开旧页面。

## 模型与密钥

可从 DeepSeek、OpenAI 或兼容 API 预置开始，配置服务地址、模型名、密钥、视觉输入及 JSON 模式。

在模型设置中点击“测试连接”，可用少量固定文本检查当前地址、协议、模型和密钥是否能返回结果。仅在点击时发送一次请求，不发送学习材料、不保存密钥，也不自动重试；服务商可能按其 API 计费。测试文本通过不代表图片或整份讲义生成一定成功。

| 接口 | 可用于 |
|---|---|
| Chat Completions | DeepSeek、OpenAI，以及提供该兼容接口的服务或本机模型 |
| Responses | 提供 Responses 协议的服务 |

这表示协议兼容，不表示任意模型都支持图片或结构化输出。使用自定义模型时按其能力调整“视觉输入”和“JSON 模式”。不支持图片的模型无法处理纯扫描页。

浏览器输入的密钥只保留在当前页面和执行中的任务内，不写入浏览器存储、任务记录或导出文件。也可以复制 `.env.example` 为 `.env`，在本机填写；CLI 会加载它。`LEARNMARGIN_API_KEY` 只用于匹配 `LEARNMARGIN_BASE_URL` 的端点，DeepSeek 与 OpenAI 环境密钥也只用于各自配置的端点。

材料先在本机解析；点击生成后，所选内容会发送到所配置的 API。知识点模式会分析全部勾选材料；页码模式会读取主材料指定范围，并分析其他勾选参考文件以检索相关内容。检索按完整单元分批进行，选出生成所需内容，并在导出包中保存检索位置与依据。修改配置和导入文件不会自动调用模型。

## 材料支持

| 材料 | 范围单位与处理方式 |
|---|---|
| PDF | 文件页码；抽取文字并保留逐页图像，支持视觉模型读取扫描页 |
| PPTX | 幻灯片；文字、表格、图片与备注 |
| DOCX | 标题与连续内容段；不把解析出的段落冒充打印页码 |
| EPUB | 按阅读顺序的章节及内嵌图片 |
| TXT / Markdown / HTML / CSV 等 | 按连续内容段；不会加载外部网页素材 |
| PNG / JPEG / WebP / TIFF 等 | 图片或帧；需要视觉模型理解内容 |
| DOC / PPT / ODT / ODP / RTF | 需要另行安装 LibreOffice，经本机转换为 PDF |

PPTX 的复杂图表、公式、SmartArt，以及 DOCX 的浮动布局可能无法完整提取。对这些材料，可先导出 PDF 后导入。旧 Office 转换是可选功能；项目当前的真实联调环境没有安装 LibreOffice，自动测试验证了缺依赖和超时处理。

单文件最多 50 MB、一次最多 8 份材料。每次导入最多 300 个内容单元；主材料与相关参考内容合计最多 60 个单元、10 万文本字符和 40 张图片。过大的范围会要求缩小，不静默截掉教材。知识点或参考资料检索最多分析 300 个单元、40 万文本字符。

正文生成后会再次对照本节材料审校前提、来源转述、重复内容和侧栏答案。模型识读与审校仍可能出错，尤其是模糊手写、涂改和相似数学符号；它们不构成任意字迹、任意公式都能正确识别的保证。

## Skill 内容与打包

技能的独立使用说明见 [`USAGE.md`](skills/learnmargin/USAGE.md)，智能体入口是 [`SKILL.md`](skills/learnmargin/SKILL.md)，参考资料入口是[全书方法地图](skills/learnmargin/references/book-map.md)。

内置的是原创转述的方法摘要与应用设计，包含适用情境、学习动作和边界，不含完整电子书或用户教材。软件直接读取同一份方法库，先选相关章节再设计提示。修改通用学习规则时，同步更新 skill、应用生成规则与受影响的验证。

```shell
uv run python scripts/package_skill.py
```

独立技能包生成于 `dist/learnmargin-skill.zip`。

## 开发与检查

```shell
# 后端
uv run learnmargin
# 另一个终端启动前端；代理到8765
npm --prefix frontend run dev

uv run ruff check src tests scripts
uv run pytest
npm --prefix frontend test
npm --prefix frontend run build
uv run python scripts/check_skill.py
uv run --with pymupdf python -m pytest -q tests/test_skill_sidebars.py
uv run python -m build
uv run python scripts/package_skill.py
uv run python scripts/verify_package.py
```

真实浏览器端到端检查见 [`frontend/e2e/smoke.py`](frontend/e2e/smoke.py)，只用内置示例，不触发付费生成。需要显式验证真实 API 时：

```shell
uv run python scripts/smoke_api.py --live
uv run python scripts/smoke_api.py --live --mode pages
```

这会把项目自编示例发送到当前 API，可能产生费用；记录保存在被 Git 忽略的 `artifacts/` 中。CI 不读取真实密钥或调用付费模型。

CI 会运行 Python 检查与测试、前端测试和构建、技能检查、软件与技能打包，并在仓库外的独立环境安装 wheel、启动服务和生成无 API 示例。打包检查记录保存在 `artifacts/package-verification.json`。设计和接口见 [架构说明](docs/ARCHITECTURE.md)、[接口契约](docs/CONTRACT.md)，贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与部署

服务默认仅绑定 `127.0.0.1`，定位为单用户本机应用。材料、生成任务与产物存放在系统的 LearnMargin 用户数据目录，可用 `LEARNMARGIN_DATA_DIR` 指定其他位置。任务元数据会保存；进程重启后，未完成任务标记为中断，不假装自动恢复 API 请求。

暂不包含公网多用户认证、配额计费或云端隔离，不能把当前本地服务直接作为公共网站部署。

## 方法与接口来源

- 学习方法：Barbara Oakley《学习之道》，详见[知识库来源与边界](skills/learnmargin/references/book-map.md)。
- API：DeepSeek [接入文档](https://api-docs.deepseek.com/)、[视觉输入](https://api-docs.deepseek.com/guides/vision/)；OpenAI [结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。
- 数学排版：[KaTeX](https://katex.org/)，资源及 MIT 许可证随项目保存；字体和其他依赖依其各自许可证使用。

项目代码采用 [MIT License](LICENSE)。
