# 模型接入：OpenAI 与 DeepSeek

本作品当前实现的是后端 API Key 接入，三个字段由 `.env` 读取，密钥不会送到浏览器。2026-10-27 检查时，本机尚未配置密钥，未向真实生成模型发送论文内容。

## ChatGPT 能不能用

可以使用 OpenAI 的模型 API。ChatGPT 网页订阅和 API Key 是不同的接入与计费路径，不能把网页登录状态当作作品的 API 密钥。

最新官方文档也提供“Sign in with ChatGPT”：符合资格的用户可以通过专门授权，让应用使用 ChatGPT 计划的额度。该方案需要应用实现登录、授权范围、令牌管理与符合接口要求的推理请求。本作品目前尚未接入这一授权流程，不能直接复用当前 Codex 会话的凭证。

依据：[OpenAI 开源应用接入指南](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)、[使用 ChatGPT 计划的授权说明](https://learn.chatgpt.com/docs/sign-in-with-chatgpt)。可用资格和范围以用户账号与官方当前规则为准。

若有 OpenAI API Key，可在 `.env` 设置：

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=填写你自己的API密钥
LLM_MODEL=填写你账号可用且支持Chat Completions与JSON输出的模型名
```

模型名不预填，避免把 ChatGPT 界面模型名或不适用的型号误当成 API 型号。当前通用适配器使用 `temperature=0`、`max_tokens`、`response_format=json_object`；若选用的模型要求不同参数，需要补充对应适配，不能保证所有 OpenAI 型号开箱即用。

## DeepSeek 接入步骤

1. 在 [DeepSeek 开放平台](https://platform.deepseek.com/) 自行创建 API Key，并确认账号可调用所选模型。
2. 打开项目根目录 `.env`，填写以下三行。密钥只保存在本机，不发送到聊天里，不提交 Git。

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=填写你自己的API密钥
LLM_MODEL=deepseek-flash
```

截至核对日，官方首个 API 调用文档使用 `deepseek-flash`；旧教程中的 `deepseek-chat` 不能作为当前型号依据。适配器仅对官方 `api.deepseek.com` 添加 `thinking.type=disabled`，适合本阶段受预算约束的 JSON 抽取与段落翻译。

依据：[DeepSeek 首次调用](https://api-docs.deepseek.com/quick_start/agent_integrations/)、[思考模式参数](https://api-docs.deepseek.com/guides/thinking_mode/)。价格、额度及型号更新请以官方页面为准。

3. 在项目根目录运行一次小请求检查（会使用少量 API 额度）：

```powershell
.venv312/Scripts/python.exe -X utf8 scripts/check_model.py
```

此脚本只发送固定连接检查提示，不发送论文。成功时显示“模型 JSON 接口连接成功”。错误只输出异常类别，不回显密钥或服务商响应正文。

4. 停止原服务并重新运行：

```powershell
.venv312/Scripts/python.exe scripts/dev.py
```

5. 在界面“连接与配置”确认“已配置”，再依次验证：一段英文的中文翻译、一个带数字与引用的段落、两篇论文的单一比较维度。界面“已配置”仅表示三个字段齐全，不等于已经成功调用过模型。

## 验收顺序

- 问答：每条陈述必须有有效证据 ID，新增数字会被拒绝；语义是否得到原文支持仍需人工核对。
- 翻译：先选一段，检查术语、否定词、数字、公式和引用。再做本页，最后做全文。数字数量检查与占位符保护不等于翻译质量保证。
- 多文比较：逐格展开证据，确认所报硬件/参数属于该论文、该模型和该训练阶段。不能直接按不同实验条件的数值排名。
- 自动测试中的模型回答全部是注入样例，不代表上述真实模型已通过质量验收。

翻译默认限制每任务 300 个内容块、30 万字符。通过 `SCHOLARTRACE_TRANSLATION_MAX_BLOCKS` 可调整块数（最高 1000），长书建议按页码区间调用接口。接口文档在本机 `/docs`；代码、公式候选、表格和书目保留原文，尚不支持整本教材版式翻译或 PDF 重排。
