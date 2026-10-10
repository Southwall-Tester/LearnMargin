# 思考设置适配

能力表在 `src/learnmargin/reasoning.py`，前端从 `/api/settings` 读取同一份表。按规范化后的完整服务地址、协议、完整模型 ID 匹配；不凭模型名称前缀猜测第三方转发服务的行为。以下是已接入的范围，不代表所有模型的能力清单。

| 服务与模型 | 协议 | 可选设置 |
| --- | --- | --- |
| DeepSeek 官方 `deepseek-flash`、`deepseek-v4-pro` | Chat Completions / Responses | none、low、high、max |
| 智谱 / Z.ai 通用 API：`glm-5.3`、`glm-5.3-flash`、`glm-5.3-flashx` | Chat Completions | low、high、max；不可关闭 |
| 智谱 / Z.ai 通用 API：`glm-4.7` | Chat Completions | 开启 / 关闭 |
| OpenAI 官方 `gpt-6-astra`、`gpt-6.1-sol` | Chat Completions / Responses | low、medium、high、xhigh、max；不可关闭 |
| OpenAI 官方 `gpt-6-sol`、`gpt-6-luna` | Chat Completions / Responses | none、low、medium、high、xhigh、max |
| OpenAI 官方 `gpt-5`、`gpt-5-mini`、`gpt-5-nano` | Chat Completions / Responses | minimal、low、medium、high |
| OpenAI 官方 `gpt-5.1` | Chat Completions / Responses | none、low、medium、high |
| OpenAI 官方 `gpt-5.2`、`gpt-5.2-2025-12-11` | Chat Completions / Responses | none、low、medium、high、xhigh |

DeepSeek 接受根地址和 `/v1`；OpenAI 为 `https://api.openai.com/v1`；智谱和 Z.ai 分别为 `https://open.bigmodel.cn/api/paas/v4` 和 `https://api.z.ai/api/paas/v4`。其他地址、Coding Plan 路由、未知模型及尚未登记的快照只显示“不指定”，不妨碍按服务默认配置使用。

GPT-6 四款模型均按官方精确 ID 登记，不把 `gpt-6` 当作可推断的别名。应用发送文本/图像和 JSON 输出要求，不发送工具调用；因此这里的 Chat Completions 适配不代表支持 GPT-6 的 Chat 工具调用。GPT-6 Astra / 6.1 Sol 的工具调用要求 Responses；GPT-6 Sol / Luna 的 Chat 工具调用仅支持 none。后续若增加工具，必须另行校验这些限制。

所有模型都可“不指定”，此时不发送任何思考参数，不再隐式给 DeepSeek 关闭思考。DeepSeek Chat 的 none 转成 `thinking.type=disabled`，档位转成 `thinking.type=enabled` 加 `reasoning_effort`；Responses 使用 `reasoning.effort`。GLM 开关只发送 `thinking.type`，不会伪造 low/high 等级。OpenAI 已登记的推理模型在 Chat 中使用 `max_completion_tokens`，连接测试沿用同一种字段。

模型、地址或协议更改后，界面清除旧档位并提示；其他设置变更保留思考选择。恢复偏好时保留有效选择，清除不兼容选择并提示。后端独立检查显式选择，不兼容时在发送请求前报错，不自动降级、不更换模型。用户选择进入识读缓存配置摘要；密钥不进入摘要。

只支持 token 预算的接口需要独立适配其参数、范围与协议，目前不暴露虚构的预算档位。新增适配须在能力表登记确切地址、协议、模型和原生值，附官方依据，补上有效参数、无效参数和默认不发送参数的测试。不要仅扩大全局枚举就透传所有值。

官方依据（核对于 2026-10-10；服务可能后续变更）：

- [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)及 [Responses API](https://api-docs.deepseek.com/api/create-response/)。
- [Z.ai GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)、[官方模型说明](https://huggingface.co/zai-org/GLM-5.3-Flash)、[GLM-5 官方仓库](https://github.com/zai-org/GLM-5/blob/main/README_zh.md)、[思考开关](https://docs.z.ai/guides/capabilities/thinking-mode)。
- [智谱 GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)、[智谱 HTTP 接口](https://docs.bigmodel.cn/cn/guide/develop/http/introduction)、[Z.ai 接口](https://docs.z.ai/api-reference/introduction)。
- [OpenAI 推理指南](https://developers.openai.com/api/docs/guides/reasoning)、[GPT-5.1](https://developers.openai.com/api/docs/models/gpt-5.1)、[GPT-5.2](https://developers.openai.com/api/docs/models/gpt-5.2)。
- [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol)、[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)。

验证使用模拟 API 请求和真实本地浏览器；不以此宣称第三方服务当前可用、付费端到端成功、思考 token 数量或生成速度已经验证。
