# 第二十一阶段实测：多文对比的引文绑定（0.18.0）

日期2026-10-29。本轮处理M2-QUALITY中比较值与所附引文不一致的问题，新增有界候选记录、历史与导出。结果仍为原文短语候选，M2-QUALITY保持部分实现。

## 开发前反例与真实探查问题

在改动前用同一合成段落构造3种提案：从另一句取4 V100、把A100换成H100、把pretraining换成fine-tuning。旧检查均给`model_source_checked`，因为数字只需存在于整个候选块。记录在[baseline_failures.json](../output/stage21/baseline_failures.json)，**这是注入的开发反例，不是3次真实模型错误**，调用数0。

第一份[冻结v1](../evaluation/freeze_stage21_comparison_quotes_v1.json)后运行零模型比较：DistilBERT硬件无匹配；CLIP把背景句的`19 GPU years`匹配成`19\nGPU`。该错误保留在[首次检索](../output/stage21/retrieval_first/results.json)。原文在介绍他人的计算量，既不是设备数量，也不是CLIP训练配置。

随后增加通用时间量排除与显存限定词识别，未写入论文名或预期答案，保存[冻结v2](../evaluation/freeze_stage21_comparison_quotes_v2.json)。[第二次零模型检索](../output/stage21/retrieval_v2/results.json)分别取到`8 16GB V100 GPUs`和`592 V100 GPUs`；这只说明这两个已观察样本的原文匹配改变，不构成归属或语义质量通过。上述过程没有付费调用。

## 产品与测试

值和条件改为同一实际引文内的连续原文短语；一格一个来源，保留可逆Unicode字符范围，只对实际引文做数字/残片检查。未知、重复、超长或未采用的模型提案保留状态和原因。首次正常响应与来源一起保存，超限时显式标记截断。

新增比较历史、JSON导出、实际短引文展示及默认关闭的模型开关。旧结果保留旧规则标签。生成后和历史/导出检查确切ParseRun、原块文字/页码/坐标及引文范围，失效时不返回；不会静默切到最新版本。

最终全量 **472 passed，1 warning，71.16秒**；新增32项，包括3种旧反例、跨来源、重复维度、歧义位置、空白可逆范围、128不能截为8、超长留存、响应/导出/历史一致、删除/历史清除、生成期间版本消失、偏移篡改以及8项设备数量/时间量检查。警告为既有Starlette/httpx弃用提示。

初次专项43项和全量463项通过，后来补充原文范围篡改、非对象输出和GPU years等反例；第一轮零模型暴露了当时测试未覆盖的单位误取，不能以先前通过数证明算法质量。补齐后最终472项通过。没有将失败HTTP改为空成功。

最终TypeScript/Vite通过，构建2.98秒；比较按需包约8 kB、主包291.54 kB、阅读器491.83 kB（未gzip）。测试与构建日志随本轮输出保存。

## 两篇官方论文的首次生成

使用默认库的SQLite备份，仅复制两份官方PDF并保留原ID/ParseRun，其他文件路径重定向到隔离目录、其他文献在副本隐藏；移除副本中的后台模型任务并禁用订阅。原库未被替换。输入如下，已在阶段11等记录中观察，**不是留出集**：

| 文件 | SHA256 | 原ParseRun |
|---|---|---|
| 1910.01108v4.pdf | a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6 | parse_828fd9e7162c4192a1c9ebe64b1ac885 |
| 2103.00020v1.pdf | 6478b6e571a7d6fcd846d8ef77bfd60c285f1986abb8f475eedc43de403074f5 | parse_d0e4dfdbf04c452581344e082be2f609 |

40份产品/运行文件、原块摘要及比较请求在调用前固定。仅比较训练硬件，每篇一次，无付费重试，总上限2次/5000输出token预留。实际输入3182、输出151 token，未报告用量0次。

| 文献 | 首次保留值 | 同引文条件短语 | 实际引文位置 |
|---|---|---|---|
| DistilBERT | 8 16GB V100 GPUs | DistilBERT | PDF第3页；原块字符171—241 |
| CLIP | 592 V100 GPUs | RN50x64 | PDF第5页；原块字符1708—1781 |

两格均为`quote_bound_candidate`、`semantic_verified=false`；字符范围从0计，右端不包含。仅说明这两份首次响应能按规则绑定短语，不计算答案准确率、拒答率或独立语义支持率。

原始记录：[请求与系统提示](../output/stage21/live_first/provider_requests.json)、[首次模型JSON](../output/stage21/live_first/provider_first_responses.json)、[保存结果与历史/导出一致检查](../output/stage21/live_first/results.json)、[预算](../output/stage21/live_first/budget.json)。脚本[verify_comparison_quotes.py](../scripts/verify_comparison_quotes.py)默认零模型，并拒绝覆盖结果目录。

## 页面与本机作品

生成后页面检查发现载入历史没有同步文献/维度勾选；只修改ComparisonPanel.tsx同步选择，单独保存[UI v3](../evaluation/freeze_stage21_comparison_quotes_ui_v3.json)。后端与提示未变，之后0次付费请求；v1/v2不被覆盖，也不冒称在v3重新生成。

将唯一这份真实官方比较追加到默认库前，再比对两篇PDF哈希、原块摘要和全部锚点；默认14篇、13篇就绪、762页、18733块不变。没有导入合成反例或早期错误结果。证明在[local_demo.json](../output/stage21/local_demo.json)。用户可从“多文对比→比较历史”读取该结果，查看不调用模型。

最终[浏览器检查](../output/stage21/ui_checks.json)及截图：[桌面](../output/screenshots/stage21-comparison-quotes-desktop.png)、[原页回跳](../output/screenshots/stage21-comparison-source.png)、[手机](../output/screenshots/stage21-comparison-quotes-mobile.png)。页面展示短语、同引文条件与真实引文，载入历史后同步两篇文献及硬件维度。CLIP回跳v1第5页原文块，定位框为整块范围。390×844手机的clientWidth/scrollWidth为375/375，表内为294/650，只在表内横向滚动；旧记录警示、关闭的模型开关和控制台0个error已核对。视口已恢复、测试页已关闭，用户原标签保留。默认8000/5173已更新0.18.0。

## 未完成范围

同一引文仍可能同时描述多种配置、否定或他人工作；字面出现不能证明条件归属、因果关系、比较可比性或事实完整性。短语严格抽取可能拒绝合理译文、改写或跨段事实；硬件规则是有限词形集合，不能覆盖所有设备和计算量单位。没有完整NLI、跨文事实裁决或独立人工质量结论。

总验收仍有20个必要项未通过，公网、最终PDF/视频、仓库评审访问及其余质量缺口继续保留。版本号和472项软件测试不代表整体参赛作品完成。
