# 第十阶段：冻结后新作品检查

日期：2026-10-28。产品保持0.8.0、提交`b7dc401`；本阶段仅新增评测脚本、题集和记录，没有根据新结果修改backend/app代码，也没有重试挑选更好答案。

## 范围与限制

按作品新增 [DistilBERT v4](https://arxiv.org/abs/1910.01108v4) 与 [CLIP v1](https://arxiv.org/abs/2103.00020v1)，二者未用于前几轮产品问题调优。精确版本通过既有arXiv流程导入，本地索引在问答前完成。题目和标准事实由同一开发助手在读论文后编制，模型可能在预训练中见过这些公开论文；不是盲测，也没有独立人工裁决。

| 来源 | 页数 | 实际解析状态 | 索引片段 | PDF SHA256前12位 |
|---|---:|---|---:|---|
| DistilBERT v4 | 5 | ready | 69 | a70c78e2b4db |
| CLIP v1 | 48 | partial：1页表格提取失败，原生正文仍可用 | 762 | 6478b6e571a7 |

首次导入脚本要求ready，因此遇到CLIP的partial时停止。核对告警后保留partial状态，修改的是验收准备脚本的接受/记录范围，未修改产品解析器，也未将失败表格说成成功。重跑导入复用已存在PDF，后续问题均有明确原生正文依据。

题集`qa_holdout_v3.json`包含8道可回答草稿题（6单篇、2跨文、10个严格标注块组）和2项独立记录的缺失字段检查。产品先冻结，再编题；编题期间未调用/ask。`freeze_holdout_v3.json`在首次问答前固定题集与10个产品/评测文件哈希，Git HEAD为b7dc401，新增评测脚本未提交，因此manifest如实标记dirty。`git diff b7dc401 -- backend/app`为空。

在首次检索前，已在题集中固定6个真实模型采样ID：N01、N03、N04、N06、N07、N08，未根据首轮成绩换题。所有标准事实和缺失依据都不发送给问答API。

## 一次新集检索

运行`data/stage10_holdout_retrieval/20261028T071821Z-59ee783b`，紧凑报告`evaluation/results/stage10-retrieval.json`。

| 范围 | 严格标注组命中/分母 | 锚点有效/返回 |
|---|---:|---:|
| 单篇6题 | 6/6 | 36/36 |
| 跨文2题 | 2/4 | 12/12 |
| 总计8题 | 8/10 | 48/48 |

全部使用已准备的BM25+Dense+RRF，接口无错误。顺序HTTP及本地核验p50=0.1650s、p95=0.3261s，n=8，nearest-rank；本机暖缓存小样本，不是并发性能结论。

原生块组并非所有等价证据的穷尽标注。80%的严格块组命中不能解释成80%的答案准确率，也不能与第九阶段已调优回归集22/23直接比较为同一分布的提升。两道跨文题仍各缺一个指定原文块。

## 一次真实模型采样

运行`data/stage10_holdout_live/20261028T071849Z-11a478a0`，6题各一次DeepSeek生成，`deepseek-flash` / `api.deepseek.com`，未调提示、未重试、未换服务商。

- 6/6返回模型模式、无降级；6题均保留陈述，但这不等于6题正确。
- 36/36锚点有效，14/14保留陈述引用有效ID；8个标注组中命中6个。
- p50=2.3637s、p95=7.9077s，n=6。
- 独立人工复核0/6，完整正确率、已作答准确率和引用语义支持率均保持null。

开发助手定性排查（未替代独立人工裁决）：

| 题目 | 观察 |
|---|---|
| N01 学生架构 | 报告移除部件，并给每两层取一层的初始化说明；额外列出用户未要求的具体层数/参数量缺失，仍需检查“层数变化”是否清楚回答 |
| N03 语料/硬件/耗时 | 覆盖语料、8张16GB V100及约90小时；额外要求更细硬件型号和精确耗时，是过度保守的缺失声明 |
| N04 数据集与查询 | 覆盖4亿图文对、50万查询；来源保留具体构建条件 |
| N06 两种CLIP模型硬件 | 保留RN50x64与最大ViT的不同GPU数量/天数 |
| N07 跨文硬件 | 只保留DistilBERT设置，将CLIP RN50x64字段列为缺失 |
| N08 跨文目标 | 保留DistilBERT损失，但漏CLIP目标；额外出现不完整的`Lce = ^{P}`公式表述，来源ID与数字检查未能识别这类语义/数学错误 |

此次样本表明0.8.0仍存在跨文多字段覆盖和生成相关性问题。上标表示只避免部分字符拼接，不等于完整公式恢复。保留这些新失败，后续若据此修复，这批题应转入开发回归，不能继续当未见测试集。

## 两项缺失字段检查

问题在首次问答前固定：DistilBERT的GPU核心时钟MHz、CLIP的GPU运行电压。开发助手检索了全文提取文本中的相应术语，并核对硬件段落；这仍不是独立人工的“全文没有报告”证明，尤其CLIP有表格候选失败。

运行`data/stage10_missing_fields/20261028T071934Z-a12bbff1`。两项均返回模型模式，未保留事实陈述，并给出相应字段在给定来源中未报告的说明。不能仅从最终空claims区分模型主动拒答与生成后的来源/数字过滤，因此报告使用`no_retained_factual_claims`，来源标记为`unknown_model_or_validation_filter`；不计算“拒答准确率100%”。术语从原始响应离线更正，没有再次付费请求。

本阶段总共8次显式付费生成（6正例+2缺失检查），输出预留上限12,800 token；不是实际token账单或费用统计。检索/索引仅本机运行。完整候选原文响应留在data，Git仅保存紧凑结果、指纹和pending复核模板。

## 复现与检查

~~~powershell
# 显式下载确切版本、保留partial警告并准备本地索引；不调用生成模型
.venv312/Scripts/python.exe -X utf8 scripts/prepare_holdout_corpus.py
# 检索一次，不调用生成模型
.venv312/Scripts/python.exe -X utf8 scripts/evaluate_qa.py --pack evaluation/qa_holdout_v3.json --freeze evaluation/freeze_holdout_v3.json --output data/new_holdout_run
# 以下会产生API请求；重新运行将不再是首次样本
.venv312/Scripts/python.exe -X utf8 scripts/evaluate_qa.py --pack evaluation/qa_holdout_v3.json --freeze evaluation/freeze_holdout_v3.json --cases N01 N03 N04 N06 N07 N08 --live-model --output data/new_holdout_live
.venv312/Scripts/python.exe -X utf8 scripts/evaluate_missing_fields.py --live-model
~~~

新增3项工具检查，确认检索降级不当拒答、未保留陈述不当语义正确、保留陈述也不自动判正确。完整套件200 passed、1 warning，24.30秒；术语调整后这3项另行复验通过，没有重新调用模型。记录见data/stage10_tests_final.txt。应用前后端与第九阶段提交一致，因此未重复前端构建。

正例pending模板在evaluation/results，使用review_evaluation.py对本机原运行离线复核，不能重新问一遍后裁决另一份回答。负例目前仅独立记录现象，不混入正例正确率分母。CLIP与DistilBERT全文不加入Git或审阅包；来源作者/版本及下载出处可查stage10-corpus.json。

下一步是基于开发集做按字段的跨文补检索与回答相关性检查，再以其他新作品验证；还需要独立人工金标准、消融、并发测试、技术PDF更新、录像与公网鉴权部署。本阶段没有把材料提交主办方，也没有替用户填写参赛身份。
