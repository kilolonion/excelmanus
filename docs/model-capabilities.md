# 模型能力目录

此表由 `scripts/check_model_catalog.py --write-doc` 从 `excelmanus/model_catalog.json` 生成。请修改 JSON 并核对来源，不要直接编辑此表。

核验日期：2026-09-25。以下是官方模型资料，不是当前账户或端点的实测结果。未知值保留为未知。

模型窗口、输入限制、HTTP/文件传输限制和本地预算分开记录；实际预算取适用限制的最小值，显式用户预算单独标记。未知型号默认本地预算为 32,000，不构成模型能力承诺。

客户端目前接通文本、图片输入和文本输出。音视频官方能力仅作资料展示；手动声明不会启用缺失的输入链路，原生音视频消息会被明确拒绝。

| 模型 | 提供商 | 官方窗口 | 输入/传输约束 | 最大输出 | 官方输入 | 思考控制 | 工具 | 状态 | 来源 |
|---|---|---:|---|---:|---|---|---|---|---|
| `gpt-4o` | openai | 128000 | — | 16384 | text/image | 不支持 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-4o) |
| `gpt-4o-mini` | openai | 128000 | — | 16384 | text/image | 不支持 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-4o-mini) |
| `gpt-5` | openai | 400000 | — | 128000 | text/image | 支持：minimal/low/medium/high；不可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5) |
| `gpt-5.1` | openai | 400000 | — | 128000 | text/image | 支持：none/low/medium/high；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.1) |
| `gpt-5.2` | openai | 400000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.2) |
| `gpt-5.2-codex` | openai | 400000 | — | 128000 | text/image | 支持：low/medium/high/xhigh；不可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.2-codex) |
| `gpt-5.3-codex` | openai | 400000 | — | 128000 | text/image | 支持：low/medium/high/xhigh；不可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.3-codex) |
| `gpt-5-codex` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5-codex) |
| `gpt-5.1-codex` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.1-codex) |
| `gpt-5.1-codex-mini` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.1-codex-mini) |
| `gpt-5.1-codex-max` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.1-codex-max) |
| `gpt-5.3-codex-spark` | openai-codex | 128000 | — | 未核实 | text | 支持；不可关闭 | 未核实 | active | [官方1](https://learn.chatgpt.com/docs/changelog) |
| `claude-sonnet-5` | anthropic | 1000000 | — | 128000 | text/image | 支持：low/medium/high；可关闭 | 支持 | active | [官方1](https://platform.claude.com/docs/en/models/sonnet-5/overview) |
| `claude-sonnet-4-6` | anthropic | 1000000 | — | 128000 | text/image | 支持：low/medium/high；可关闭 | 支持 | active | [官方1](https://platform.claude.com/docs/en/models/sonnet-4-6/overview) |
| `claude-haiku-4-5` | anthropic | 200000 | — | 64000 | text/image | 支持；可关闭 | 支持 | active | [官方1](https://platform.claude.com/docs/en/models/haiku-4-5/overview) |
| `claude-sonnet-4` | anthropic | 200000 | — | 64000 | text/image | 支持；可关闭 | 支持 | retired | [官方1](https://platform.claude.com/docs/en/about-claude/model-deprecations) |
| `gemini-2.5-flash` | gemini | 1048576 | — | 65536 | text/image/video/audio | 支持；可关闭 | 支持 | active | [官方1](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash) |
| `gemini-2.5-pro` | gemini | 1048576 | — | 65536 | text/image/video/audio | 支持；不可关闭 | 支持 | active | [官方1](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro) |
| `gemini-2.5-flash-lite` | gemini | 1048576 | — | 65536 | text/image/video/audio | 支持；可关闭 | 支持 | active | [官方1](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite) |
| `qwen-plus` | qwen | 1000000 | 995904 | 32768 | text | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-plus) |
| `qwen-flash` | qwen | 1000000 | — | 32768 | text | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-flash) |
| `qwen-max` | qwen | 32768 | 30720 | 8192 | text | 不支持 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-max) |
| `qwen-turbo` | qwen | 131072 | 98304 | 16384 | text | 支持；可关闭 | 不支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-turbo) |
| `qwen-long` | qwen | 10000000 | 1000000 | 8192 | text | 不支持 | 不支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-long) |
| `qwen-coder-plus` | qwen | 131072 | 129024 | 8192 | text | 不支持 | 不支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen-coder-plus) |
| `glm-4.5` | glm | 128000 | — | 96000 | text | 支持；可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5) |
| `glm-4.7` | glm | 200000 | — | 128000 | text | 支持；可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.7) |
| `glm-4.6v` | glm | 128000 | — | 未核实 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v) |
| `kimi-k2.6` | moonshot | 256000 | — | 未核实 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart) |
| `MiniMax-M2` | minimax | 204800 | — | 128000 | text | 支持；关闭方式待核 | 支持 | active | [官方1](https://platform.minimax.io/docs/guides/text-generation) |
| `M2-her` | minimax | 64000 | — | 未核实 | text | 未核实 | 未核实 | active | [官方1](https://platform.minimax.io/docs/guides/text-generation) |
| `deepseek-flash` | deepseek | 1000000 | — | 384000 | text/image | 支持；可关闭 | 支持 | active | [官方1](https://api-docs.deepseek.com/quick_start/pricing/) |
| `deepseek-v4-pro` | deepseek | 1000000 | — | 384000 | text | 支持；可关闭 | 支持 | active | [官方1](https://api-docs.deepseek.com/quick_start/pricing/) |
| `deepseek-v4-flash` | deepseek | 1000000 | — | 384000 | text/image | 支持；可关闭 | 支持 | redirected | [官方1](https://api-docs.deepseek.com/quick_start/pricing/) |
| `mimo-v2.6-flash` | mimo | 1000000 | — | 128000 | text/image/video/audio | 支持；可关闭 | 支持 | active | [官方1](https://mimo.mi.com/models/zh-CN/mimo-v2.6-flash) |
| `mimo-v2.6-pro` | mimo | 1000000 | — | 128000 | text/image/video/audio | 支持；可关闭 | 支持 | active | [官方1](https://mimo.mi.com/models/zh-CN/mimo-v2.6-pro) |
| `grok-build-0.1` | xai | 256000 | — | 未核实 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://docs.x.ai/developers/models/grok-code-fast-1) |
| `grok-4.7` | xai | 500000 | — | 未核实 | text/image | 支持：low/medium/high；不可关闭 | 支持 | active | [官方1](https://docs.x.ai/developers/models) |
| `doubao-seed-2-1-pro-260915` | doubao | 1048576 | — | 262144 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://docs.volcengine.com/docs/ark/model-list?lang=zh) |
| `doubao-seed-2-1-pro-260628` | doubao | 262144 | — | 262144 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://docs.volcengine.com/docs/ark/model-list?lang=zh) |
| `anthropic/claude-sonnet-4` | openrouter | 200000 | — | 64000 | text/image | 支持；可关闭 | 支持 | active | [官方1](https://openrouter.ai/api/v1/models) |
| `gpt-6-astra` | openai | 1050000 | — | 128000 | text/image | 支持：low/medium/high/xhigh/max；不可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| `gpt-6-sol` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-6-sol) |
| `gpt-6-luna` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| `gpt-5.6-sol` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| `gpt-5.6-terra` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.6-terra) |
| `gpt-5.6-luna` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.6-luna) |
| `gpt-5.6` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh/max；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| `gpt-5.6-cyber` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.6-cyber) |
| `gpt-5.5` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.5) |
| `gpt-5.4` | openai | 1050000 | — | 128000 | text/image | 支持：none/low/medium/high/xhigh；可关闭 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5.4) |
| `gpt-5-pro` | openai | 400000 | — | 272000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5-pro) |
| `gpt-5-mini` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5-mini) |
| `gpt-5-nano` | openai | 400000 | — | 128000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-5-nano) |
| `gpt-4.1` | openai | 1047576 | — | 32768 | text/image | 不支持 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-4.1) |
| `gpt-4.1-mini` | openai | 1047576 | — | 32768 | text/image | 不支持 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-4.1-mini) |
| `gpt-4.1-nano` | openai | 1047576 | — | 32768 | text/image | 不支持 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/gpt-4.1-nano) |
| `o1` | openai | 200000 | — | 100000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o1) |
| `o1-pro` | openai | 200000 | — | 100000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o1-pro) |
| `o3` | openai | 200000 | — | 100000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o3) |
| `o3-mini` | openai | 200000 | — | 100000 | text | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o3-mini) |
| `o3-pro` | openai | 200000 | — | 100000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o3-pro) |
| `o4-mini` | openai | 200000 | — | 100000 | text/image | 支持；关闭方式待核 | 支持 | active | [官方1](https://developers.openai.com/api/docs/models/o4-mini) |
| `claude-fable-5-1` | anthropic | 1000000 | — | 128000 | text/image | 支持；不可关闭 | 支持 | active | [官方1](https://platform.claude.com/docs/en/models/overview) |
| `claude-opus-5-5` | anthropic | 1000000 | — | 128000 | text/image | 支持；不可关闭 | 支持 | active | [官方1](https://platform.claude.com/docs/en/models/overview) |
| `claude-sonnet-4-5` | anthropic | 200000 | — | 64000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/sonnet-4-5/overview) |
| `claude-opus-4-5` | anthropic | 200000 | — | 64000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/opus-4-5/overview) |
| `claude-opus-4-6` | anthropic | 1000000 | — | 128000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/opus-4-6/overview) |
| `claude-opus-4-7` | anthropic | 1000000 | — | 128000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/opus-4-7/overview) |
| `claude-opus-4-8` | anthropic | 1000000 | — | 128000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/opus-4-8/overview) |
| `claude-opus-5` | anthropic | 1000000 | — | 128000 | text/image | 支持；可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/opus-5/overview) |
| `claude-fable-5` | anthropic | 1000000 | — | 128000 | text/image | 支持；不可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/fable-5/overview) |
| `claude-mythos-5` | anthropic | 1000000 | — | 128000 | text/image | 支持；不可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/mythos-5/overview) |
| `claude-mythos-5-1` | anthropic | 1000000 | — | 128000 | text/image | 支持；不可关闭 | 支持 | legacy | [官方1](https://platform.claude.com/docs/en/models/mythos-5-1/overview) |
| `gemini-3.8-flash` | gemini | 1048576 | — | 65536 | text/image/video/audio | 支持：low/medium/high；不可关闭 | 支持 | active | [官方1](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) [官方2](https://ai.google.dev/gemini-api/docs/thinking) |
| `glm-5.3` | glm | 1000000 | — | 128000 | text | 支持：low/high/max；不可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3) |
| `glm-5.3-flash` | glm | 1000000 | — | 128000 | text/image/video | 支持；不可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash) |
| `glm-5.3-flashx` | glm | 1000000 | — | 128000 | text/image/video | 支持；不可关闭 | 支持 | active | [官方1](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash) |
| `kimi-k3` | moonshot | 1000000 | — | 1048576 | text/image/video | 支持：low/high/max；不可关闭 | 支持 | active | [官方1](https://platform.kimi.com/docs/guide/kimi-k3-quickstart) |
| `MiniMax-M3` | minimax | 1000000 | — | 未核实 | text/image/video | 支持；关闭方式待核 | 支持 | active | [官方1](https://platform.minimax.io/docs/guides/text-generation) |
| `qwen3.8-max` | qwen | 1000000 | 983616 | 131072 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen3-8-max) [官方2](https://help.aliyun.com/zh/model-studio/deep-thinking) |
| `qwen3.8-flash` | qwen | 1000000 | 983616 | 131072 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen3-8-flash) [官方2](https://help.aliyun.com/zh/model-studio/deep-thinking) |
| `qwen3.7-plus` | qwen | 1000000 | 983616 | 131072 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen3-7-plus) [官方2](https://help.aliyun.com/zh/model-studio/deep-thinking) |
| `qwen3.7-flash` | qwen | 1000000 | 983616 | 131072 | text/image/video | 支持；可关闭 | 支持 | active | [官方1](https://help.aliyun.com/zh/model-studio/qwen3-7-flash) [官方2](https://help.aliyun.com/zh/model-studio/deep-thinking) |

## 证据规则

1. `official_documentation` 是型号资料。地区、协议、聚合路由和订阅权限须另核；`endpoint_verified` 不会因文档或远端列表成功而变成 true。
2. 实测记录绑定真实 ID、端点、协议、凭证摘要、自定义头、思考模式和额外请求体，并带探测版本及有效期。禁止复用规范名或其他账户的缓存。
3. 工具实测需完成合法参数调用与随机确认码回传；图片实测需识别随机图案内容。HTTP 成功、配置选中、未返回推理摘要都不是支持/不支持的充分证据。
4. 用户覆盖标记为 `user_override`，不能冒充实测。旧探测版本不再作为能力证据使用。
5. `legacy_context_hints` 仅供名称匹配，不供运行时推断上下文、模态或能力；新增型号必须补充精确来源和核验日期。
6. `/models` 中的上游字段保存在 `remote_declarations`，不覆盖官方资料；嵌入、语音/图像专用及已知无工具型号从 Agent 候选列表中分离。
7. retired 仅在匹配的原生供应商阻止添加；redirected 可添加并保留真实上游 ID。
8. `/probe context` 仅查询上游声明的窗口元数据，不发送百万 token 填充请求、不将请求成功解释为真实窗口。

## 更新流程

打开精确型号的官方原文，核对 ID、快照、地区和协议，更新 JSON 的能力字段、source_urls 和 verified_at；无法确认的字段写 null。运行目录检查、回归测试并重新生成本文。API 与前端从同一 JSON 读取数据，无需同步修改多份预设。

## 本次修复验证

后端能力契约、配置、探测、协议适配和会话同步相关定向测试：**239 passed**；前端能力目录、模型设置、思考选择器和模型选择相关测试：**65 passed**。TypeScript 检查和 Next.js Webpack production build 通过。Python wheel 已构建，并确认包含 `excelmanus/model_catalog.json`。

目录自检：`.venv/bin/python scripts/check_model_catalog.py`。完整仓库还有其他未提交工作区改动；这里的定向回归集只覆盖本次模型能力修复。
