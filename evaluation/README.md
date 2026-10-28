# 固定问答评测草稿 v1

这是第八阶段历史基线。当前0.8.0已修改产品文件，默认v1冻结应拒绝在新代码上运行；复现原结果需使用阶段8提交`eb2659f`。当前回归使用`qa_regression_v2.json`与`freeze_regression_v5.json`，全部题目都已观察，不再称留出。过程与命令见`../docs/STAGE9_TEST_REPORT.md`。不得覆盖本文件所述的v1题集和首次结果。

20 道中文事实题：8 道开发回归，12 道按作品隔离的留出题；18 道单篇、2 道跨文。所有题目可回答，**未覆盖无答案识别率**。题目和标准事实由开发助手整理，尚无独立人工复核，不是盲测或正式成绩。新论文是公开基础论文，也可能存在于模型预训练数据中。

开发来源是官方素材中的 Transformer v7、BERT；新增来源是 [RoBERTa v1](https://arxiv.org/abs/1907.11692v1) 与 [T5 v4](https://arxiv.org/abs/1910.10683v4)。仅保存改写后的事实、页码、坐标与文本指纹，不在仓库分发论文全文。SHA256 必须与实际文件一致。同一作品的版本、别名、相同文件不能跨开发/留出两侧。别名与标题规则仍不能保证发现所有出版源的未知同作品关系。

每个 gold group 是一个必须检索的标注块组，组内可以有等价块；v1 当前每组仅指定一个块，不是穷尽性的证据标注。**严格块召回偏保守**：找到别处的等价事实可能仍计未召回；数字命中、ID有效和块召回都不等于事实正确率。

## 使用顺序

1. 启动本地服务，并按 README 导入官方 Transformer/BERT。
2. 执行 `scripts/prepare_eval_corpus.py`，通过现有 arXiv 流程下载两个确切版本。获取失败时不替换成别的版本。
3. 使用已提交 `qa_v1.json` 和 `freeze_v1.json`；**不要重新生成或覆盖已有冻结文件**。`build_eval_pack.py` 是本机首次制作记录，含首次解析 UUID，另一台机器无需重新执行。
4. `evaluate_qa.py` 将按 PDF 哈希匹配文件、固定当前 ParseRun，再核对所有标准块的文本哈希、页码和坐标。跨机器重新解析可能不同；此时明确报错，应另建评测版本，不手动把失败改成通过。
5. 检索默认无模型调用。真实模型必须显式选择，最多8题/次、每题至多一次生成请求、输出上限1600 token；无重试。不记录密钥或服务商响应头。

~~~powershell
.venv312/Scripts/python.exe -X utf8 scripts/prepare_eval_corpus.py
.venv312/Scripts/python.exe -X utf8 scripts/evaluate_qa.py
.venv312/Scripts/python.exe -X utf8 scripts/evaluate_qa.py --split holdout --cases H01 H02 H06 H07 H11 H12 --live-model
~~~

脚本仅支持 loopback API。新运行写入带UTC时间和随机标识的独立目录；`raw.json` 包含原文候选，仅留本机 `data/`。紧凑 `report.json` 不含候选原文。6道生成题是在首轮检索后选定，再调用模型；不是预注册的随机抽样。不要反复跑到满意后只报告最好一次。

## 复核不会再次付费调用

复制本机运行目录中的 `review-template.json`，逐题对照 `qa_v1.json` 的标准事实和固定版本原文，再检查 `raw.json` 的完整回答、缺失说明及引用。保持 case/response 哈希不变；填写真实复核人、`status=reviewed`、`judgment=correct/partial/incorrect/abstain/error`、`supported_claims` 和理由。完整正确要求所有所问字段及条件正确、无冲突缺失声明、没有实质性额外错误；相关背景不算回答了问题。标准事实也需要独立复核，不能盲从本稿。

~~~powershell
.venv312/Scripts/python.exe -X utf8 scripts/review_evaluation.py --run data/stage8_evaluation/你的运行目录 --reviews data/你的复核.json --output data/复核报告.json
~~~

此命令读取原结果，不调用API，输出拒绝覆盖。未完全复核时完整正确率保持 null；检索模式不计作拒答。真实模型的降级/错误与拒答分开。正确率分母为全套被评测题，已作答准确率分母为确实保留陈述的题；只有整个被评测集合完成复核后才计算。引用语义支持需逐条核验，不能从 `source_checked` 推断。

## 首轮结果与后续

见 `results/retrieval-v1.json`、`results/live-sample-v1.json` 和 `../docs/STAGE8_TEST_REPORT.md`。代码与题集在请求前按文件哈希冻结，运行时工作树未提交，manifest 如实记录 dirty 状态及当时 HEAD；不冒称已有独立发布提交。

该留出集在观察结果后应退役为回归集。下一轮需要新作品/题目、独立复核和更完整的等价证据组，再比较检索与重排策略。原计划60题、无答案题、语义支持标注、消融和并发性能尚待完成。

## 新作品v3首次记录

第十阶段在冻结产品b7dc401后另建`qa_holdout_v3.json`与`freeze_holdout_v3.json`，覆盖DistilBERT/CLIP的8题及单独记录的2项缺失字段检查。采样ID在首次检索前固定；来源、partial警告和全部结果见`../docs/STAGE10_TEST_REPORT.md`。只在冻结的0.8.0上记录一次，不按新结果修复后继续冒称未见集。缺失检查用`evaluate_missing_fields.py --live-model`，空陈述不自动判正确，也不推断一定是模型主动拒答；来源过滤也可能导致最终为空。
