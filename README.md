# 研迹 ScholarTrace

基于版本化证据链的科研助手。应用当前是**本地开发版本（0.8.0）**，已接入意图任务、预算与检查点恢复、模型连接诊断，延续证据写作框架、CSV 科学绘图、架构图与可复现导出，保留有原文锚点的引用图谱、版本选择、局部指标和综述证据提纲，延续文献发现、全文入库、主题追踪、多文比较、OCR 与混合检索。六模块仍在逐步实现。

**第九阶段修复**：[索引与上标使用说明](docs/INDEX_AND_LAYOUT.md)、[实测报告](docs/STAGE9_TEST_REPORT.md)。新增中文字段/章节定位、逐文候选分配、显式本地索引进度，以及保留上标的模型输入。已观察回归集严格标注组命中22/23，197项自动测试通过；不代表新题事实准确率。第八阶段PDF仍是0.7.0历史草稿，正式提交前需按最终版本更新。

**第八阶段新增评测和参赛材料草稿**：[20题评测说明](evaluation/README.md)、[实测报告](docs/STAGE8_TEST_REPORT.md)、[技术说明源稿](docs/TECHNICAL_DESIGN_DRAFT.md)、[7分钟演示脚本](docs/DEMO_SCRIPT.md)、[提交清单](docs/SUBMISSION_CHECKLIST.md)。首轮严格标注块组召回12/23；6题真实模型样本存在缺项和拒答，独立人工质量复核尚未完成。技术PDF在output/pdf，封面按用户意见暂不写参赛身份。尚未录制MP4或部署公网。

## 当前功能与边界

| 已实现 | 当前边界 |
|---|---|
| 批量上传、持久化文献库、后台进度、删除 | 本地单用户开发版，尚未加入公网鉴权 |
| PDF 原生文字、内容块、目录与坐标 | 复杂表格/公式目前是候选识别，尚未保证完整还原 |
| 文件哈希与内容重合/版本提示 | 版本新旧证据不足时不自动覆盖 |
| 本地扫描 OCR、行坐标、识别分数和原图定位 | 需要显式准备模型；OCR 数字和公式仍需核对，分数不是准确率 |
| PDF 画布、翻页缩放、OCR文字覆盖层、目录和证据回跳 | 数字引用/References 双向跳转；作者年份格式暂保留列表 |
| 表格行列/表头/空值、单元格坐标、HTML显示 | 多层表头、无框表格仍有拆列/合并问题，结果标为未核验候选 |
| BM25＋多语言 Dense＋RRF、工具步骤记录 | Rerank 与完整语义核验待接入 |
| 重新解析与历史 ParseRun | 历史证据保留原解析锚点，不随新解析漂移 |
| 多文事实矩阵、逐文逐维检索、每格证据与 Markdown 导出 | 原文匹配与模型抽取分开标注；未配置模型时不自动补齐空缺 |
| 段落/页/全文翻译任务、术语表、缓存、失败重试、历史和原译导出 | 需配置模型；数字/公式保护不能代替质量评测，长书受任务预算限制 |
| 中英对照、段落锚点双向滚动 | 尚未实现词级悬停翻译、PDF 译文重排 |
| arXiv/Crossref 检索、标识符/作者/年份核对、元数据来源 | 标题候选需人工确认；元数据不能替代全文 |
| arXiv 具体版本 PDF 入库、哈希去重与来源关联 | 其他出版方仅提供核对入口，不自动抓取任意链接 |
| 主题检查、已见版本、点赞/点踩与解释理由 | 最近20条窗口，单机服务运行时才自动检查；推荐未做质量评测 |
| 引用图谱、版本选择、入度/PageRank、共引与年份视图 | 只计算所选直接引用子图，注册记录对应不等于语义核验或全球影响力 |
| 综述证据提纲、未来工作原文及后续候选历史 | 尚未生成学术综述或判定方向已解决；元数据不替代全文 |
| 写作框架、证据段落、历史版本、注册书目与导出包 | 固定模板；论点语义、创新性与实验仍待核验 |
| CSV 六类图表、SD/SEM、明确清洗和归一化、图注与重画 ZIP | 演示数据为虚构；不执行自由代码或推导因果 |
| 受控架构图、PNG/SVG/PDF/Mermaid 与重画脚本 | 设计草图不代表已实施验证 |
| 受控研究任务、规则/模型计划、步骤轨迹、预算与恢复 | SQLite 状态机；尚未完整 LangGraph 自主重规划，模型输出仍需语义核验 |
| 本机双击配置、连接诊断、OpenAI/兼容参数适配 | 密钥只在本机；连接成功不代表所有任务质量合格 |
| 可选大模型回答、证据ID与数字检查、失败降级 | 有效引文不等于语义一定正确，需核对原文 |

图谱为直接书目引用基线；写作与 CSV 已有固定模板和受控规则，自主语义写作与自然语言分析仍待完善；追踪和反馈推荐为有界基线，翻译接口已接入，DeepSeek 已完成真实小样本检查，BERT 多字段问答仍有部分保守缺失；完整质量评测待完成，界面明确标识开发阶段。开发计划见根目录详细实施方案及 `docs/PROGRESS.md`。

## 本机运行（Windows PowerShell）

本机已验证 Python 3.12、Node.js 24、uv 和 pnpm。旧 Anaconda Python 3.11 的 VC DLL 与本机 ONNX Runtime 不兼容，保留在 `.venv/` 供阶段一回退；当前使用独立 `.venv312/`。首次安装：

```powershell
uv venv --python 3.12 .venv312
uv pip install --python .venv312/Scripts/python.exe -r requirements.lock.txt
Set-Location apps/web
pnpm install --frozen-lockfile
Set-Location ../..
Copy-Item .env.example .env
.venv312/Scripts/python.exe scripts/dev.py
```

打开 http://127.0.0.1:5173 。API 文档在 http://127.0.0.1:8000/docs 。按 Ctrl+C 停止两个服务。若端口已被本项目占用，使用已有进程，不重复启动。

macOS/Linux 的 Python 路径为 `.venv312/bin/python`，其余目录相同；本阶段尚未做跨系统实测。

### 本地 OCR 与语义模型

```powershell
.venv312/Scripts/python.exe -X utf8 scripts/prepare_local_models.py --ocr
.venv312/Scripts/python.exe -X utf8 scripts/prepare_local_models.py --semantic
```

OCR 权重由固定版本的 RapidOCR wheel 提供并校验；语义模型约 220MB，显式准备时从模型发布源下载到 `.cache/embedding/`。正常上传、健康检查和问答不会下载模型。论文内容不会发送到模型下载服务。

在 `.env` 开启 `SCHOLARTRACE_ENABLE_OCR=true`、`SCHOLARTRACE_ENABLE_TABLES=true`、`SCHOLARTRACE_ENABLE_DENSE=true`。已导入的扫描件通过文献菜单“重新解析”升级；每次创建新的 ParseRun，历史内容继续可定位。

较长文献未完成语义索引时会明确降级到 BM25，避免首次提问阻塞。可显式预索引：

```powershell
.venv312/Scripts/python.exe -X utf8 scripts/prepare_semantic.py --document-id 你的文献ID
```

当前语义模型是轻量多语言 MiniLM，便于无 GPU 本机运行；后续将以固定问答集比较 BGE-M3 等模型，不能把模型相似度当事实置信度。

### 模型配置（可选）

在 `.env` 填入 OpenAI-compatible Chat Completions 服务的配置：

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-secret-key
LLM_MODEL=your-available-model
```

OpenAI、ChatGPT 订阅授权与 DeepSeek 的区别和具体配置见 [模型接入步骤](docs/MODEL_SETUP.md)。可用 `.venv312/Scripts/python.exe -X utf8 scripts/check_model.py` 发送固定小请求检查连接。

重启 API 后生效。密钥仅在后端使用，不能提交到 Git。未配置模型时可以完整使用文献管理、PDF 阅读和证据检索；返回值会明确为 `evidence_only`，不会把检索片段包装成模型答案。

### 自建测试素材

```powershell
.venv312/Scripts/python.exe scripts/create_sample_pdfs.py
```

输出位于 `data/samples/`。这些 PDF 的论文、作者、数字和书目都是**明确标注的虚构软件测试数据**，不是官方赛题素材。`manifest.json` 保存文件哈希和预期事实。通过上传入口选择样例即可验证流程；损坏样例用于异常测试。

已接入官方 `pdf/`：阶段一完成 v1/v7 去重与 541 页原生解析；阶段二已对扫描版全 15 页做真实 OCR，并检查历史锚点保留。表格和混合检索的实测范围见阶段测试报告，公式 LaTeX 恢复仍未完成。

可以通过 CLI 检查并选择性导入自己的素材，不改变原文件：

```powershell
.venv312/Scripts/python.exe -X utf8 scripts/inventory_corpus.py --directory "你的/pdf/目录"
.venv312/Scripts/python.exe -X utf8 scripts/import_corpus.py --directory "你的/pdf/目录" --names 1706.03762v7.pdf 1706.03762v1.pdf
```

使用 `--help` 查看完整参数，批量导入全部资料需要显式选择 `--all`。

## 引用图谱与综述证据

侧栏打开“引用图谱”，选择已解析的文件。默认仅选常见短论文，排除自建几何测试和长书；相同 arXiv 作品默认选较新版本，可手动覆盖。

- 构图从 PDF 书目中的 DOI/arXiv 标识符出发，勾选注册源核对后最多处理 60 项。注册记录、待核对标识符和身份冲突分开呈现，不用向量相似度造引用边。
- 点击节点看入度/出度、局部 PageRank 与相邻关系；点击边看原书目和正文引用位置，直接回到指定 ParseRun 原文。
- “发表年份视图”按注册源首次发表年排布，未知年份保持未核对。“只看一跳”和用途筛选只改变视图。
- 引用用途可在阅读证据后人工标注，系统不会把它显示为自动验证结果。指标仅反映当前所选子图，不是全球影响力。
- “综述证据提纲”列出本地全文摘录与未来工作方向。后续检索会标出同名来源、较早论文、同年及日期未知记录，结果保留为待核验候选。
- JSON 导出保存图谱与证据，Markdown 导出包含原文、版本、页码、块 ID 和后续候选记录。历史快照保留，来源删除后失效。

实际样例为 4 个来源作品、53 个节点、59 条书目引用边；其中 49 个外部节点仅有注册元数据。范围和限制见 [第五阶段报告](docs/STAGE5_TEST_REPORT.md)。

## 发现与追踪

侧栏打开“发现与追踪”，输入标题、参考文献、DOI 或 arXiv ID。标题命中只是候选；核对作者、年份和版本后再勾选确认并获取 PDF。References 列表中的“查找书目”会预填原始片段，尚未完整归并的续行需要人工补全查询。

- arXiv 记录能获取具体版本 PDF；Crossref 记录保持仅元数据。下载后进入现有解析流程，来源与 SHA256 可在 `/api/documents/{id}/provenance` 查询。
- 点赞/点踩会影响下一次检索的共享词排序与默认隐藏；可以显示已点踩并撤销。当前没有声称官方代码或实验复现成功。
- “主题追踪”保存关键词；默认自动检查关闭，可手动检查。启用后仅在本机 API 服务运行时每日检查，重启后继续到期任务。
- 第一次成功建立基线，后续区分新作品/版本；只覆盖最近更新的20条，不能保证全量。失败不改变成功记录，1小时后再自动尝试。
- 可选 `SCHOLARTRACE_CONTACT_EMAIL` 配置 Crossref 的真实联系邮箱；不填也能走公共池。

本阶段实测与限制见 [第四阶段报告](docs/STAGE4_TEST_REPORT.md)。真实 API 验证脚本默认只做标识符与标题检索，下载与主题保存需显式参数。

## 多文对比与联读

- 文献库有两篇以上可检索文献时，点击“多文对比”，选择 2—5 篇及比较维度。展开单元格证据可定位指定 ParseRun 原文，缺失事实保持空缺。
- PDF 正文数字引用有下划线热点，可跳转到 References；组合引用先选条目，再跳转。点击“返回正文引用处”回到阅读位置。“引用”页签也列出反向入口。
- “中英对照”显示按内容块对齐的原文、译文和任务历史；配置模型后可翻译选中段落、本页或全文。代码、公式、表格与书目保持原文。已完成任务可导出 HTML/Markdown。
- 翻译失败保留已完成段落，点击重试；服务重启后恢复处理中任务。任务绑定模型身份、ParseRun 和术语表，模型变更需新建任务。
- 本轮真实文献与浏览器验收范围见 [第三阶段报告](docs/STAGE3_TEST_REPORT.md)。

## 验证

```powershell
.venv312/Scripts/python.exe -m pytest
.venv312/Scripts/python.exe scripts/verify_stage1.py
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage3.py
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage4.py
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage5.py --resolve-metadata
Set-Location apps/web
pnpm build
```

`pytest` 使用隔离临时目录，默认关闭 OCR/Dense，由专项测试显式启用或注入模型；运行中的本地文献不会被覆盖。`verify_stage1.py` 面向关闭自动 OCR 的基线服务；已开启 OCR 的服务使用 `verify_stage2.py` 验证。后者会给指定官方样例创建新的解析记录，不修改原 PDF。

## 目录

```text
apps/web/          React + TypeScript 工作台与 PDF 阅读器
backend/app/      FastAPI、SQLite 仓储、PDF 解析与证据问答
scripts/          联合启动、自建素材、在线 smoke 验证
tests/            隔离接口与来源约束测试
docs/             阶段进度、实测报告
data/             本地 PDF、解析数据库与任务记录（Git 忽略）
```

当前采用 SQLite WAL 与本地受限进程池，以免要求参赛者先安装 Docker，并避免 PyMuPDF 的多线程限制。数据结构保留作品、版本、文件和解析记录；后续接入 PostgreSQL、向量索引与独立任务队列。

## 数据、许可与公开部署

原始文件、解析文本和工具记录保存在 `SCHOLARTRACE_DATA_DIR`。本阶段服务默认仅监听 127.0.0.1。加入身份认证、用户隔离、HTTPS 和任务限流前不直接暴露到公网。

第三方组件的许可分别适用；PyMuPDF 的 AGPL/商业授权模式需要在公开发布时明确处理，项目本身暂未选择开源许可证。第三方论文/教材文件不得因为可以上传就自动放入公开仓库。参赛者信息在提交阶段填写，不生成虚构团队信息。

## 写作与科学绘图

打开左侧“写作与绘图”，选文献生成证据框架，编辑后保存版本，可查看历史或回到 PDF。CSV 可导入自己的文件或使用明确标注的自建数据；设置列、单位、聚合、重复标识与误差条后出图。架构图编辑节点与连接即可导出。

复现 ZIP 带原始 CSV、数据哈希、配置、处理后 CSV、图注与同一份绘图脚本。解压安装依赖后运行 python render.py，输出在 reproduced/。示例见 examples/；详细记录见 [第六阶段验收报告](docs/STAGE6_TEST_REPORT.md)。

## 研究任务（0.7.0）

侧栏“研究任务”可把一次请求串为原文证据、对比、图谱、写作框架和流程图。先生成计划，再执行；可在步骤边界暂停、取消，或调整预算后继续。任务固定来源解析版本，已完成结果不重做。联网下载、翻译和 CSV 统计规则仍使用对应模块。

不会配置模型时，先看 [双击配置说明](docs/模型配置_双击操作.md)。本机已连接 DeepSeek，真实模型开发小样本的结果与已知缺失见 [第七阶段验收](docs/STAGE7_TEST_REPORT.md)。这些结果不是独立测试集，也不代表全部问题都能正确回答。
