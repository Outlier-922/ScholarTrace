# 必要能力收尾：20篇引用图谱与漏边修复

客户端日期2026-11-01；基线188dd38 /0.25.0，修复后0.25.1。[操作指南](GRAPH_LOCAL_IDENTITY.md)。本轮聚焦M5的批量规模、引用身份与节点指标，不进行生成模型调用，也不将图指标当学术质量结论。

## 输入先固定，首次结果保留

官方目录预选20篇不同论文，680页、约42MB，排除541页书籍、重复修订版/扫描版和重复Emergent文件。[原始清单](../evaluation/freeze_stage33_corpus_v1.json)在上传前固定文件SHA、页数和104个代码/驱动文件。HTTP一次20文件上传，3份复用、17份新导入；17篇ready、3篇partial，全部680页均有保存记录。PPO与LoRA各2页表格提取失败，CLIP沿用既有1页表格失败；这些状态没有清除，也不称完整结构化通过。

现有库从14份/762页/18733块变成31份/1312页/33732块，原14份及其ParseRun未变，长书5867向量保留。本轮没有重新解析旧论文或再次生成旧结果。

[20份实际解析快照](../output/stage33/baseline/sources.json) · [上传结果](../output/stage33/baseline/uploads.json) · [解析原始日志](../output/stage33/stage33_ingest_first.txt)。

在首个图谱之前，开发助手从原PDF参考文献核对并记录[9组开发对照](../evaluation/graph_batch_pairs_v1.json)，含5组带编号、4组无编号引用。[基线冻结](../evaluation/freeze_stage33_graph_baseline.json)固定20份文件/ParseRun/完整块摘要，关闭注册源查询，节点上限150。

首个实际图谱`02a7c613-ef83-49c1-ad7a-b0754a9e8e0d`：20来源、150节点、227边，其中80条进入指标；9组仅命中4组。4组无编号书目没有可用的标识符，另1组目标全文文件未带arXiv身份而与外部目标分裂，不能靠文件名补造对应。

## 产品改动及同范围重跑

新增通用第一页标题/姓名见证：从原标题块合并相邻同字号续标题，提取其后可定位的首位作者姓名行；只接受当前所选全文中唯一的完整标题＋首位作者字面匹配。标为`local_title_author_matched`，保留目标原块、页码、坐标、ParseRun和`cited_version_status=not_determined`，不伪造注册记录或被引版本。标题相似、首作者不符、多候选、多标识符或注册冲突仍待核对。

第一次修复探查为8/9，发现末组参考文献续行被原解析器标成table_candidate，构图过滤时丢失。该块在第13页的真实编号书目内，是原生参考文献文本，不是表格。补充受限的原生书目续行恢复：已有编号条目、未编号姓名行、年份及出版/URL信号须同时满足；生成表格仍排除，原块及其类型不修改。

前端支持新状态及目标标题/作者原文核对，新增只看本地全文关系；筛选隐藏待核对外部节点、视图可按规模调整，保存的指标不随视图重算。旧v2图谱快照保持原结果，新图谱为citation-graph-v3。

[初次探查](../output/stage33/stage33_prototype.txt) · [续行恢复探查](../output/stage33/stage33_prototype_recovery.txt) · [修复冻结](../evaluation/freeze_stage33_graph_fixed_v2.json)。v2冻结106文件、相同20份原文/ParseRun、同一9组对照和原注册缓存摘要；没有另换较容易的样本。

修复后实际图谱`3ce28fd0-4895-4feb-8717-ce78c6ee2ce1`：

| 项目 | 基线 | 修复后 |
|---|---:|---:|
| 来源作品 | 20 | 20 |
| 节点预算/实际节点 | 150/150 | 150/150 |
| 全部书目边 | 227 | 255 |
| 可计算边 | 80 | 108 |
| 其中本地标题/作者对应 | 0 | 28 |
| 9组已观察开发对照 | 4/9 | 9/9 |
| 原有可计算关系丢失 | — | 0 |

修复后56条可计算边连接本地全文；147条未对应的标识符边排除在指标外。1059组待解析候选含315组节点预算未展开。9/9是已观察开发对照，不是独立准确率或全量召回。

[基线原图](../output/stage33/baseline/graph.json) · [修复后首次图](../output/stage33/fixed/graph.json) · [逐组对照](../output/stage33/pair_checks.json)。

## 独立复算、原文及失败日志

50个可计算节点，按唯一引用者→被引者对构造转移矩阵；悬挂行均匀分配、阻尼0.85，使用NumPy线性方程求解复算，而非调用产品幂迭代。基线最大绝对差4.73e-12、修复后2.24e-12，分数和约1；入/出度逐节点重计，原有80条可计算关系均保留。

逐项核对两图1268个书目/正文锚点及69个目标身份锚点：对应原块、页码、坐标；正文检查原offset/marker与上下文，书目/目标检查原文逐字一致。20份块摘要和全部31份PDF原始哈希匹配，注册缓存未改变。注册状态、引用用途及学术语义没有由数值检查自动升级。

[独立指标结果](../output/stage33/pagerank_checks.json) · [本地节点CSV](../output/stage33/local_node_metrics.csv) · [完整核对](../output/stage33/verification_final.json)。

使用Poppler渲染并实际查看第13页书目与目标第1页，确认书目续行、完整标题和首作者可读，未修改PDF。目标页渲染有Symbol字体提醒，标题/姓名文字可读；不据此声称所有数学符号渲染完美。[原书目页](../output/stage33/pdf_checks/dpo-reference-page13.png) · [目标标题/作者页](../output/stage33/pdf_checks/target-title-author-page1.png)。

首个构图本身完成，但验证驱动误将导出视图附加的`newer_source_runs=[]`字段当作保存快照变化而断言失败。修正比较后只读核对原图，没有重跑或覆盖基线。[首次驱动失败](../output/stage33/stage33_graph_baseline.txt)。独立锚点驱动也初次误读正文引用为text而不是context；改为核对上下文、offset和marker，首次失败保留，[初次核对](../output/stage33/stage33_audit_first.txt)。这些驱动修正不是原论文或图谱结果修订。

## 软件与实际界面

新增10项合成边界检查，覆盖标题续行及姓名证据、相似/截断标题拒绝、机构排除、无编号本地边、原始ID别名、本地冲突、多候选/多编号、原生书目续行和生成表格排除。定向56通过；全量**652 passed**，1项既有Starlette/httpx弃用警告，103.08秒；TS通过，Vite构建3.69秒。合成用例不充当科研质量标注。

实际界面打开保存的20篇图谱，过滤为本地20节点，可查看新增关系、完整书目续行和目标身份原文控件；键盘Enter选节点已验证。390×844时对话框329/329，无根部横向溢出，控制台0错误；没有点击新构图、注册查询或模型按钮。视口恢复、临时标签关闭，原用户标签保留。

[概览](../output/screenshots/stage33-20-papers-overview.png) · [原文身份依据](../output/screenshots/stage33-identity-evidence-desktop.png) · [窄屏](../output/screenshots/stage33-20-papers-mobile.png) · [浏览器记录](../output/stage33/browser_checks.json)。

## 剩余项

20篇规模和所列引用/指标检查已完成。M5整体仍部分实现：CLIP、Codex、Emergent三篇尚无完整首作者身份见证，无标识符关系可能继续遗漏；待解析候选和节点截断保留，引用用途、全量实体对应及学术分析尚未全部验收。PageRank不是领域影响力/质量证明，也不能直接判定基石或创新方向。

必要缺口仍19（14功能部分实现、5交付未齐），submission_ready=false。正式技术PDF、视频、公网及评审源码访问仍待完成，没有凭新增测试数量宣告整体完工。
