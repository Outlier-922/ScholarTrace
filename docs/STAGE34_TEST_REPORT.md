# 阶段34：首页首作者依据与漏边复核

日期2026-11-01，产品0.25.2，基于提交a735dca。此次只处理阶段33已有的首页身份缺口，不新增赛题外模块。

## 原问题与冻结

同20篇官方论文、680页、相同PDF哈希和ParseRun，旧图谱ID为`3ce28fd0-4895-4feb-8717-ce78c6ee2ce1`。旧17个可用身份候选中并非全部正确。

修改前先固定[基线与三篇标注](../evaluation/freeze_stage34_header_baseline.json)。两篇首行包含很多作者，旧规则把整行当成过长姓名而跳过；另一篇原生姓名—邮箱名单被归为较高的表格候选。探查又发现一篇旧规则跳过首行、误选后续作者组，第四项不冒充预先冻结或独立测试。最终代码、106个文件哈希、四个标注及零请求预算在[正式冻结](../evaluation/freeze_stage34_header_fixed_v2.json)，正式构图在冻结后执行。

| 官方文件 | 修改前 | 本次候选与原文 |
|---|---|---|
| 2103.00020v1.pdf | 缺失 | Alec Radford，首页第一姓名行 |
| 2107.03374v2.pdf | 缺失 | Mark Chen，首页第一姓名行 |
| 2206.07682v2.pdf | 缺失 | Jason Wei，首页原生姓名—邮箱名单 |
| 2002.04745v2.pdf | 误取Yanyan Lan Liwei Wang Tie-Yan Liu | Ruibin Xiong，首页第一姓名行 |

四个原始首页已渲染并目视核对姓名：[CLIP](../output/stage34/pdf_checks/clip-page1.png)、[Codex](../output/stage34/pdf_checks/codex-page1.png)、[Emergent](../output/stage34/pdf_checks/emergent-page1.png)、[LayerNorm](../output/stage34/pdf_checks/layernorm-page1.png)。Poppler报告Symbol字体替代警告，本轮只核对标题和作者，不据此声称数学字形完整。

## 实际修改

按通用脚注/星号/机构编号切分姓名；原生较高表格候选须有连续姓名、邮箱、姓名、邮箱才允许作名单；OCR或生成文字排除。保存姓名在原块中的精确字符范围、名单类型和完整原块，不改PDF、解析块或作者注册数据。规则没有写入本批论文名、作者答案或预期引用边。

节点侧栏新增“本地全文身份依据”，展示候选和姓名原文，可跳回原始首页。标题/首作者对应仍仅为作品候选，不确认被引修订版。旧图谱保持原结果。

## 同批正式结果

新图谱`4c13cda3-3c57-46c8-9ea5-07da9a634223`：[请求及结果](../output/stage34/fixed/summary.json)、[完整JSON](../output/stage34/fixed/graph.json)。

| 项目 | 0.25.1 | 0.25.2 |
|---|---:|---:|
| 来源全文 / 页数 | 20 / 680 | 20 / 680 |
| 有身份候选的来源 | 17 | 20 |
| 节点 / 全部书目边 | 150 / 255 | 150 / 256 |
| 可计算关系 | 108 | 109 |
| 本地标题/作者对应关系 | 28 | 29 |
| 本地到本地可计算关系 | 56 | 57 |
| 待对应边（不进入指标） | 147 | 147 |
| 未解析书目组 / 预算未展开组 | 1059 / 315 | 1058 / 315 |
| 外部模型 / 注册请求 | 0 / 0 | 0 / 0 |

原108条可计算关系全部保留；新增LLaMA→Codex一条，本地源论文第12页完整书目含作者及标题但没有唯一编号，目标首页提供身份依据。九组既有开发对照仍9/9；这些是已经观察过的正例，不能计算全量召回率或独立准确率。新增边也不是独立评测集。

20份源文件和完整块摘要未变，仍17份ready、3份partial；PPO、LoRA和CLIP的原表格失败保留。主库31文献、1312页、33732块，原14篇和541页长书5867条向量保留。

## 验证与边界

全量662项软件检查通过，234.11秒，1条既有警告。新增10项覆盖姓名边界、名单/普通表格区分、OCR排除和跳过首行的回归；早期重点检查65项通过，发生在最后一个回归用例加入之前，不当作最终独立全量。最终TypeScript和Vite构建通过，Vite4.11秒。日志见[全量](../output/stage34/stage34_full_final.txt)、[TS](../output/stage34/stage34_tsc_final.txt)、[构建](../output/stage34/stage34_build_final.txt)。

独立审计检查冻结哈希、原文锚点、姓名范围、旧关系保留、度数及线性方程PageRank，见[总记录](../output/stage34/verification_final.json)、[复算](../output/stage34/pagerank_checks.json)、[四项及全部来源候选](../output/stage34/header_checks.json)。注册缓存未变，未调用生成模型或注册查询。

真实浏览器核对四个姓名面板；作者原文按钮实际回到LayerNorm首页并显示Ruibin Xiong，PDF页码1/17。390×844窄屏对话框宽329、内容宽329，无横向溢出；错误日志为空。[浏览器记录](../output/stage34/browser_checks.json) · [桌面](../output/screenshots/stage34-author-roster-desktop.png) · [原页跳转](../output/screenshots/stage34-author-source-page.png) · [窄屏](../output/screenshots/stage34-author-mobile.png)。截图不是1080p演示视频。

此轮关闭三篇缺失候选及一个旧姓名误认；20/20仅表示当前样本候选可定位，未逐一独立认证其语义或出版身份。1058组书目仍未解析，引用用途、研究路线、基石分析和整体科研质量继续按[总验收表](ACCEPTANCE.md)处理。必要缺口仍14项能力加5项交付，[交付关口](../output/stage34/acceptance_gate.json)仍未就绪。
