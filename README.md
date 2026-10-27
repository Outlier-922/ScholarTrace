# 研迹 ScholarTrace

基于版本化证据链的科研助手。当前是**第二阶段的本地开发版本**，已接入扫描 OCR、结构化表格候选和本地多语言语义检索，支持版本化解析历史与原文定位。六模块仍在逐步实现。

## 当前功能与边界

| 已实现 | 当前边界 |
|---|---|
| 批量上传、持久化文献库、后台进度、删除 | 本地单用户开发版，尚未加入公网鉴权 |
| PDF 原生文字、内容块、目录与坐标 | 复杂表格/公式目前是候选识别，尚未保证完整还原 |
| 文件哈希与内容重合/版本提示 | 版本新旧证据不足时不自动覆盖 |
| 本地扫描 OCR、行坐标、识别分数和原图定位 | 需要显式准备模型；OCR 数字和公式仍需核对，分数不是准确率 |
| PDF 画布、翻页缩放、OCR文字覆盖层、目录和证据回跳 | 沉浸式翻译与正文引用双向跳转将继续开发 |
| 表格行列/表头/空值、单元格坐标、HTML显示 | 多层表头、无框表格仍有拆列/合并问题，结果标为未核验候选 |
| BM25＋多语言 Dense＋RRF、工具步骤记录 | Rerank、多文事实矩阵与完整语义核验待接入 |
| 重新解析与历史 ParseRun | 历史证据保留原解析锚点，不随新解析漂移 |
| 可选大模型回答、证据ID与数字检查、失败降级 | 有效引文不等于语义一定正确，需核对原文 |

图谱、追踪推荐、翻译、学术写作与 CSV Copilot 尚未完成，界面中明确标识开发阶段。开发计划见根目录详细实施方案及 `docs/PROGRESS.md`。

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

## 验证

```powershell
.venv312/Scripts/python.exe -m pytest
.venv312/Scripts/python.exe scripts/verify_stage1.py
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
