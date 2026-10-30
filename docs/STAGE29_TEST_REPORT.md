# 必要能力收尾：问答数字漏检与层数误拒绝

客户端日期2026-10-30；应用0.24.1；基线ed45f93。本轮修具体问答检查问题，没有把小样本或测试数量当作品整体完成。[操作说明](QA_CANDIDATE_CHECKS.md)。

## 首次复现与修复

原数字表达式使用Unicode `\w`前边界，紧跟汉字或型号字母的数字可能不被提取；逗号分组又被拆开。实际旧代码对三个明确标注的合成反例均保留：原文只有7/Z77/65,536，却接受“使用999张GPU”“Z999”“536 tokens”。[修复前记录](../output/stage29/baseline_numeric_gaps.json)。这些是本地构造的漏洞证明，不是观察到的真实模型幻觉。

修复后数字在汉字/型号旁仍检查，千位分组和科学记数按完整字面值匹配。每行保留接受/拒绝原因、原始索引和未匹配数字范围；服务端保存完整原始模型JSON。字段范围、跨文献来源、公式残片、表格编排和20行上限继续执行。来源删除后正常历史接口隐藏原始候选及逐条细节，不能绕过删除查看旧内容。

此前N01只显示层数字段待核对且没有保留被拒绝原始行，无法追溯旧原因。本次真实输出仍触发拒绝，但新记录明确显示候选为“层数减少为原来的1/2（reduced by a factor of 2）”，未匹配数字是1。原文确实为按2倍因子缩减；这是本次检查过严，不能称模型编造层数，也不能倒推旧次失败的确切原因。

据此补上有来源短语和范围证明的缩减因子1/N表示检查，并新增不调用模型的保存响应复核。N须为整数，候选保留同一英文因子表达，仅豁免明确缩减至1/N中结构性的1；错分母、新实验数值、减少“了”1/N、明显否定及小数不走该例外。不推算层数、百分比或任意算术，仍semantic_verified=false。

## 真实调用与同一响应复核

[v1冻结](../evaluation/freeze_stage29_numeric_audit_v1.json)在调用前固定104文件，覆盖全部后端Python、前端TS/TSX/CSS、依赖清单、评测题集/驱动与新测试；原PDF、ParseRun和完整块摘要另存。选定已观察官方DistilBERT开发题N01/N03，各一次，最多2请求/3200输出token预留，服务/结构失败即停止，不自动重试。生成提示未改。

| 路径 | 实际结果 |
|---|---|
| 首次N01 / 13796fec | 部件1项保留；层数1项被字面规则拒绝，原JSON及数字1的原因保留 |
| 首次N03 / ef022aff | GPU数量8、型号V100、单卡16GB、约90小时及English Wikipedia/Toronto Book Corpus语料5项均保留 |
| 实际HTTP与usage | 2次HTTP200，预留3200，服务商实际输入3257、输出539 token，usage缺失0 |
| 同一N01候选本地复核 | 两项均成为候选；文字、原始JSON及所附证据不变，新增模型请求0，首次记录不覆盖 |
| 旧6题保存陈述回放 | 原先保留的20条仍20条通过当前字面检查，0模型请求；不能恢复旧次被拒绝原始行，不是重新生成或准确率 |

N01原文在第2页，明确去掉token-type embeddings与pooler、层数按2倍因子缩减；N03第3页明确8张16GB V100、约90小时、English Wikipedia和Toronto Book Corpus。开发助手按这两段逐项对照，没有混入同段RoBERTa的1024张/32GB/1天条件。这是已观察开发样本定性核对，不是留出准确率或独立人工质量签字。

[首次汇总](../output/stage29/live_first/summary.json) · [实际请求/响应](../output/stage29/live_first/calls.json) · [首次N01](../output/stage29/live_first/N01.json) · [首次N03](../output/stage29/live_first/N03.json) · [旧陈述回放](../output/stage29/live_first/saved_claim_replay.json) · [界面本地复核结果](../output/stage29/ui/revalidated.json)。

调用后[v2冻结](../evaluation/freeze_stage29_numeric_audit_v2.json)记录6文件变更，新增比例证明、保存候选复核及只读打开保存问答的界面；追加模型额度0。没有改生成提示、改写首次候选或再请求更好的答案。复核入口检查PDF哈希、旧解析版本、原块文字/页码/坐标/版式；不重新检索，也不调用生成器。

## 软件检查与首次失败

新增26项相关检查，最终全套**613 passed**，1项已有Starlette/httpx弃用警告，98.25秒；TS通过，最终Vite构建3.39秒。[全量日志](../output/stage29/stage29_full_v2.txt)。此前130项定向通过、605项初版全量通过；增加比例检查后第一次50项有2项失败：把数字后的句号误当小数点。修正为只阻止实际小数/千位数字后，50项全部通过，再完成最终613项。

首次受限测试进程停在部分用例、约1秒CPU且长时间无结果；核实命令行后只终止该轮两个pytest进程，保留12个进度点日志，按正常审批改用允许TestClient的权限执行。不是模型调用失败或质量分数。首次读取进程信息也被权限限制，之后通过正常审批只读核实，没有绕过审批。

[首次受限日志](../output/stage29/stage29_focused_first.txt) · [130项定向](../output/stage29/stage29_focused_second.txt) · [比例首次失败](../output/stage29/stage29_ratio_focused.txt) · [修复后50项](../output/stage29/stage29_ratio_second.txt)。负例覆盖汉字/型号数字、逗号片段、科学记数、额外数字、错分母、原文否定、错误关系、跨文献引用、旧无候选、删除隐藏和原记录保留。另有明确边界用例：原文含7不证明“7天”正确，字面通过始终不升级为语义通过。

## 实际界面、归档与整体状态

通过保存run链接打开首次1/2字段结果、模型开关默认关闭；展开后可见候选2和未匹配数字1。点击一次0模型复核后2/2字段有候选，保留原run及提示；原文回跳到第2页得到1个选中块。390×844窄屏根375/375、候选面板305/305，无横向溢出，控制台0错误。视口已恢复，临时标签关闭，用户标签保留。

[首次界面](../output/screenshots/stage29-first-rejection-desktop.png) · [复核桌面](../output/screenshots/stage29-revalidated-desktop.png) · [窄屏](../output/screenshots/stage29-revalidated-mobile.png) · [浏览器记录](../output/stage29/browser_checks.json) · [最终核对](../output/stage29/verification_final.json)。

本轮关闭上述三个数字漏检反例及本次层数候选的误拒绝，增强了实际失败可追溯性。M2-QUALITY仍部分实现：有相同数字不证明单位/条件/语义，整体问答与跨文质量尚未全面验收。必要缺口仍19（14项功能部分实现、5项交付未齐），submission_ready=false；不把新增26项检查或两题结果换算成完成百分比。
