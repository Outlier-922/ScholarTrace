ScholarTrace 第一阶段测试记录

测试日期：2026 年 10 月 24 日。应用版本：0.1.0，本地开发版。

**结果。** 本轮全量自动化回归 24 项全部通过；启动中的 API 真实 HTTP smoke 通过；官方教材第 541 页的按页结构接口只返回第 541 页的 11 个内容块。此记录验证第一阶段原生 PDF、证据定位和任务底座，不表示六模块作品已经全部完成。

**1．环境与命令。** Windows，Python 3.11.5；FastAPI 0.162.0、Starlette 1.9.0、PyMuPDF 1.28.2、pytest 8.4.2、httpx 0.28.1。解析使用 spawn 方式的 ProcessPoolExecutor，业务数据存于 SQLite WAL。测试环境设置独立临时数据目录，未使用线上真实用户资料。

```powershell
.\.venv\Scripts\python.exe -u -m pytest tests -q -o faulthandler_timeout=25
.\.venv\Scripts\python.exe scripts\verify_stage1.py
```

本轮 pytest 输出 24 个通过项，进程墙钟约 4.25 秒；这个时间包含 Python/pytest 启动，不是系统吞吐或延迟基准。受限执行环境需要允许本机回环套接字，才能初始化 TestClient 的 asyncio；通过后在相同可用环境运行。存在一条 Starlette 关于未来 httpx 适配的弃用提示，没有测试失败。

**2．自建数据与官方素材分别记录。** 自动化回归使用脚本生成的 synthetic 文件，作者、数值和书目均明确标注为虚构测试数据，不冒充官方论文或实验结果。

| 自建素材 | 用途 |
|---|---|
| synthetic_evidence_v1.pdf / v2.pdf | 两页内容，批大小 32/64，固定学习率等已知字段；检索、版本重合与坐标 |
| synthetic_retrieval_control.pdf | 同领域但不同作品的去重负例 |
| synthetic_scan.pdf | 仅图像、原生文字字符数为 0；检查 needs_ocr 状态 |
| synthetic_corrupt.pdf | 非 PDF 字节；检查清晰拒绝损坏输入 |
| geometry/ 下三个单页文件 | 90 度旋转、CropBox 裁剪、270 度旋转与裁剪组合 |

生成器为 `scripts/create_sample_pdfs.py` 和 `scripts/create_geometry_fixtures.py`。各目录的 manifest 标记 `synthetic: true`、`official_contest_data: false`；几何素材额外提供已知锚点坐标和 PNG 渲染参照。

**3．24 项自动化测试覆盖。**

| 文件 | 项数 | 主要行为 |
|---|---:|---|
| tests/test_stage1.py | 14 | 能力声明、原生上传解析、归一化坐标、原 PDF 内容、改名哈希复用、内容重合提示、不同作品负例、扫描待 OCR、损坏文件拒绝、真实证据、参考文献过滤、删除访问失效、伪造来源/新数字拒绝、历史来源失效、已完成页重处理锚点稳定 |
| tests/test_geometry.py | 3 | 三类旋转/裁剪页面的源框与未旋转 CropBox 坐标合同一致 |
| tests/test_concurrency_recovery.py | 6 | 一批同哈希只建一条记录；两版并行归入同一作品且无引用环；BrokenProcessPool 替换；调度失败保留文件并可重试；删除后迟到写入不回填；按页返回内容块和页码边界 |
| tests/test_qa_lifecycle.py | 1 | 等待模型返回期间源文献被删除，响应前清除失效证据和陈述 |
| 合计 | 24 | 本轮全量通过 |

BrokenProcessPool 使用故障注入触发替换分支，替换后由真实解析进程完成 PDF；没有故意让原生库崩溃。迟到保存与任务恢复用例验证对应的数据生命周期行为，尚未进行长时间高并发压力试验。

**4．运行中 API 的真实 HTTP 验证。** `scripts/verify_stage1.py` 在 `http://127.0.0.1:8000` 上传自建 v1、v2 和扫描件，轮询任务、读取结构并提问。结果如下：

| 检查 | 实际结果 |
|---|---|
| 自建 v1 | ready，30 个内容块 |
| 自建 v2 | ready，30 个内容块 |
| 自建扫描页 | partial，1 个图像区域，等待 OCR |
| 学习率问题 | evidence_only，1 个原文证据块 |
| 模型状态 | model_configured=false；未调用外部 LLM |
| smoke 总结果 | passed=true |

脚本默认只清理本次新建数据，遇到已存在的相同哈希文件不会删除原有记录。以上过程验证真实 HTTP、后台进程和持久化，不等于大模型问答准确率评估。

**5．官方 PDF 的原生解析记录。** 官方素材来源为 `C:\contest\2026_chuanzhi\科研小助手\pdf`，详见 `data/official_import_report.json` 的导入记录。该记录声明源文件未修改，并保存了文件 SHA256、页面数、状态、结构统计和前/中/后页抽查摘要。以下为该次记录的摘要，耗时包含上传与轮询等待，只代表本机单次运行。

| 官方文件 | 状态 | 已处理页数 | 内容块 | 单次记录耗时 |
|---|---|---:|---:|---:|
| 1706.03762v7.pdf | ready | 15/15 | 542 | 0.579 秒 |
| 1706.03762v1.pdf，上传时改名 random-upload.pdf | ready | 15/15 | 529 | 0.546 秒 |
| 1706.03762v1_img.pdf | partial，needs_ocr | 15/15 | 15 个图像区域 | 0.563 秒 |
| UnderstandingDeepLearning_02_09_26_C.pdf | ready，原生解析完成 | 541/541 | 12,003 | 18.062 秒 |

v7 先入库，随后改名上传的 v1 被识别为同一 arXiv 作品的不同版本，记录为 `same_work_version`，建议保留较新的 v7；两个文件均保留。该用例证明不依赖上传文件名，并不单独证明无标识符文献的全部语义去重质量。

541 页教材中，页面状态统计为 537 页 ready、4 页 empty。导入记录中的第 1、270、541 页原生文本分别为 674、1,527、1,166 字符。新按页接口再次读取第 541 页，实际返回 11 个块，全部 `page=541`，文本共 1,166 字符。由此确认后部页已处理且按页载入有效；目录和参考文献数量仍是启发式候选，未作为语义正确率结果。

**6．当前结论与验证边界。** 第一阶段已通过上传、原生提取、坐标、去重基线、进程调度恢复、删除生命周期、证据检索和按页加载的上述验证。当前明确未完成：

- 官方扫描件 OCR，现阶段的正确行为是 partial/needs_ocr。
- LaTeX 公式恢复与复杂表格语义重建。
- Dense/Sparse 混合检索、重排和完整语义支持核验。
- 商业/本地 LLM 的真实准确率评估；当前无配置时仅提供证据。
- 引用图谱、全文翻译、写作与 CSV Copilot 等后续模块。
- 官方全量细粒度人工标注、峰值内存、p50/p95 延迟、长时间多用户压力测试。

541 页的原生基线完成不表示 OCR、全部公式、全部标题树或参考文献解析已达到比赛要求。下一轮需要在保留这些真实记录的基础上，接入 OCR/结构化解析并建立官方问题的人工证据标注。
