# 手写与公式识读

软件当前使用用户配置的多模态 API，流程为：保留原图 → 转录文字与 LaTeX 公式、标出疑点 → 按范围检索 → 生成并对照来源审校 → 输出讲义。网页提供原页、识读文本和疑点对照；模型的判断不是经过校准的置信度。

每页完成后写入任务的 `transcription.json`，并在对应导入材料目录保存可复用缓存。后续页失败或取消不会丢掉已完成页；重新生成时按原图字节、原始文本、位置、识读规则版本、模式和非密钥模型设置校验缓存。改变超时时间或重新输入密钥不使缓存失效，删除该导入材料会一并删除缓存。失败页不会写成成功缓存，`transcription-status.json` 记录当前位置、状态与耗时。

全局规划使用完整转录，不重复附上已转录的全部原图；图表关系与不确定标记保留，分章讲解和审校仍查看原图。未转录的图像保持视觉输入。此策略降低全局规划的图片负担，不保证任意复杂页面都能在指定时限内完成；章节内容不会自动恢复，重新生成仍会重新规划与撰写。

自动模式只额外转录缺少可提取文字的图像单元。手写模式转录全部所选候选图像单元，避免印刷页脚让手写页被误当作已提取完整。页码模式不读取主文件未选中的页面；参考文件仍需检索。手写模式要求视觉输入与支持图像的模型。

## 已做的真实检查

2026-10-04，用 DeepSeek 多模态接口读取以下公开真实笔迹，并人工对照图像。两条数学式成功保留分数、角符号和度数，中文样本输出完整诗句并列出若干待核对处。样本数量很小；这不证明任意中文草写、长页、涂改或复杂公式都能正确识读，疑点的定位也仍需核对。

- 中文草写：[PaddlePaddle 官方示例](https://huggingface.co/spaces/PaddlePaddle/PP-OCRv5_Online_Demo/resolve/main/examples/handwrite_ch_demo.png)，示例库 [README](https://huggingface.co/spaces/PaddlePaddle/PP-OCRv5_Online_Demo/blob/main/README.md) 标注 Apache-2.0。
- 手写分数：[UniMERNet 样本](https://raw.githubusercontent.com/opendatalab/UniMERNet/main/asset/streamlit_demo/DirectRecognition/hwe_0000050.png)，识读为 `5/8 + 1/8 = 6/8 = 3/4`。
- 手写几何式：[UniMERNet 样本](https://raw.githubusercontent.com/opendatalab/UniMERNet/main/asset/streamlit_demo/DirectRecognition/hwe_0000083.png)，识读为 `∠BDE = ∠BED = 1/2(180°−30°)=75°`；[数据集卡](https://huggingface.co/datasets/wanderkid/UniMER_Dataset) 标注 Apache-2.0。

这些样本和本机识读记录没有纳入发行包。软件测试另外验证原页不被覆盖、转录疑点保留、手写模式不会因页脚文字跳过识读，以及关闭视觉输入时明确提示。

## 可选开源方案调研

当前没有捆绑或安装以下 OCR 模型。后续可作为独立可选识读服务接入，避免增加所有用户的安装负担。

| 方案 | 适用方向与边界 |
|---|---|
| [PaddleOCR-VL](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html) | 优先评估完整的版面分析、裁块与识读流程。官方区分完整流程与裸 VLM；Windows 支持路径及后端要求以文档为准。 |
| [PaddleOCR 公式模块](https://www.paddleocr.ai/main/en/version3.x/module_usage/formula_recognition.html) | 包括 UniMERNet 等公式模型，可识别手写公式；局部公式识别不等同于整页阅读顺序和语义理解。 |
| [MinerU](https://opendatalab.github.io/MinerU/quick_start/) | 适合文档解析；资源需求取决于运行后端。其[当前许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)含额外条件，不能简单视为标准 Apache-2.0。 |

普通文字 OCR 对中文手写的表现与印刷文本不同，且未必保留二维公式结构，可参见 [PP-OCRv5 官方评估](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5.en.md)。本地模型需要先准备依赖和权重；本次没有验证其本机速度、显存占用或离线安装过程。
