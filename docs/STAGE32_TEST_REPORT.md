# 必要能力收尾：学术草稿生成与已有候选校订

客户端日期2026-11-01；应用0.25.0；基线c24ee00。[操作指南](GROUNDED_WRITING.md)。新增的是写作页的显式模型起草与本地校订入口，原有本地框架及任务内writing默认行为保持原范围。

## 实现与一次真实结果

写作以已保存框架为输入，固定PDF哈希、确切ParseRun和现有原文，最多8条/10000字符；一次调用、2400输出token预留，原文与用户想法作为数据。六节模型JSON中事实需要单一证据ID与原文引句，数字检查限于该引句，计划明确尚未实施。成功时原子提交新草稿版本和尝试记录；未通过、失败、中断、并发版本冲突分别记录，保留解析后的原候选，不自动重试。请求ID重放和同草稿并发都不重复调用。

[初始v1清单](../evaluation/freeze_stage32_writing_v1.json)漏列根目录依赖路径；发送前修正为[106文件v2冻结](../evaluation/freeze_stage32_writing_v2.json)，加入requirements及既有写作/交接检查，没有改提示或正文。唯一真实请求为DeepSeek兼容接口deepseek-flash，HTTP200，输入1834、输出1366 token，缺失usage为0，无重试。发送的4条正文来自BERT第9/1页、DistilBERT第1/5页，绑定两份实际PDF哈希；不发送答案标注或人为期待文本。

首次候选6个事实段、6个计划段。末节把DistilBERT第1页的引句绑定到第5页证据ID，`quote_not_in_source`使整稿拒绝，v1未变。其他5条引句可逐字匹配并不代表转述完整受支持：开发助手发现BERT引言、DistilBERT两段转述还加入了所附引句之外的内容。没有把这些当作通过的学术输出。

[首次请求/响应](../output/stage32/live_first/calls.json) · [原始尝试及拒绝](../output/stage32/live_first/attempt.json) · [首次汇总](../output/stage32/live_first/summary.json)。这一个样例不是独立质量统计，也没有通过重新生成筛掉失败。

## 0新增模型的校订

观察失败后补上已有候选校订入口，没有改生成提示、原始候选或证据。校订只编辑显式候选并重新编译相同来源，成功另存版本及校订说明；跨草稿、错误版本、原文变化、无效引句仍拒绝。

[v3冻结](../evaluation/freeze_stage32_review_v3.json)固定最终代码和[校订JSON](../output/stage32/preparation/corrected_proposal.json)，此阶段模型额度0。开发助手做以下4段校订：

| 位置 | 原问题 | 修订 |
|---|---|---|
| 引言第1事实 | 附句只描述双向预训练，转述另加输出层/微调任务等细节 | 缩为双向上下文及无标注文本预训练 |
| 相关工作DistilBERT第1事实 | 转述额外加入蒸馏时机/其他工作的比较 | 缩为较小通用表示和多任务微调 |
| 相关工作DistilBERT第2事实 | 数字引句之外另加三重损失及应用判断 | 仅保留论文结论段报告的40%/60%/97%，不作为新实验结果 |
| 局限第1段 | 引句错绑第5页，且“并非针对…”含推断 | 改为待实施的目标任务/设备测量计划，不伪作原文事实 |

另存v2，5事实段附5条真实引句，7计划段。原框架、原候选及其rejected状态不变；同一尝试的reviews追加说明和结果版本。BERT真实References、DistilBERT待核对身份均未改变。逐条原文观察来自开发助手，不是独立专业评审；所有semantic_verified保持false。计划中的超参数、资源、对照和阈值仍需参赛者具体选择与实施。

[当前草稿](../output/stage32/reviewed/draft.md) · [实际导出](../output/stage32/reviewed/writing_evidence.zip) · [保留原候选的校订历史](../output/stage32/reviewed/attempt_with_review.json)。导出ZIP的三个文件是当前版本文本、真实书目和完整草稿来源；生成历史另附JSON，不能称ZIP已经包含所有尝试。

## 软件检查及实际界面

首次定向72项中3项失败，原因是测试在ASGI事件循环内嵌套调用同一个同步TestClient；改为对应生产服务调用后72项通过。增加校订检查后73项通过，TS/Vite构建通过。最终全量642项结果见归档日志；既有Starlette/httpx弃用警告保留。新增15项合成检查含错误引句、数字仅在段内而不在引句、外部ID、未实施结果、书目注入、版本冲突、文件改动、来源删除、重复/同时请求、原子回滚、失败与恢复、无模型本地校订及原候选不变。合成测试不充当真实学术质量判定。

实际界面显示v1/v2、首次未通过记录（1请求/1366输出token）、校订说明、可编辑引句和注册书目。载入原候选检查了编辑/说明/禁用新生成，再丢弃未保存预览重新打开v2，没有额外保存或请求模型。390×844对话框389/389，无横向溢出，控制台0错误。点击来源后阅读器标题切到BERT、16页、页码1；截图时PDF仍在渲染，不据此声称渲染像素/高亮已完成。临时视口恢复、临时标签关闭，用户标签保留。

[事实与引句桌面](../output/screenshots/stage32-writing-quotes-desktop.png) · [窄屏](../output/screenshots/stage32-writing-quotes-mobile.png) · [浏览器记录](../output/stage32/browser_checks.json) · [最终核对](../output/stage32/verification_final.json)。

## 完成边界

写作从固定模板推进到一次模型起草、拒绝留痕和0请求校订闭环；首次真实候选仍存在错绑/超出引句的内容，不能宣布自动学术写作质量已通过。M6-WRITING保持部分实现，其他必要项不借此升级。总必要缺口仍19（14功能部分实现、5交付未齐），submission_ready=false。正式技术PDF、MP4、公网入口与评审仓库访问仍待完成。
