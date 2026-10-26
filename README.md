# 研迹 ScholarTrace

基于版本化证据链的科研助手。当前是**第一阶段的本地开发版本**，实现真实 PDF 上传、逐页解析、版本/内容去重提示、原文阅读和可回跳的证据检索，为六模块完整系统建立底座。

## 当前功能与边界

| 已实现 | 当前边界 |
|---|---|
| 批量上传、持久化文献库、后台进度、删除 | 本地单用户开发版，尚未加入公网鉴权 |
| PDF 原生文字、内容块、目录与坐标 | 复杂表格/公式目前是候选识别，尚未保证完整还原 |
| 文件哈希与内容重合/版本提示 | 版本新旧证据不足时不自动覆盖 |
| 扫描页识别和明确提示 | OCR 尚未接入，扫描页状态为 needs_ocr，不伪装成解析完成 |
| PDF 画布、翻页缩放、目录和证据回跳 | 沉浸式翻译与正文引用双向跳转将继续开发 |
| BM25 原文证据检索、工具步骤记录 | Dense、Rerank、多文事实矩阵与完整语义核验待接入 |
| 可选大模型回答、证据ID与数字检查、失败降级 | 有效引文不等于语义一定正确，需核对原文 |

图谱、追踪推荐、翻译、学术写作与 CSV Copilot 尚未完成，界面中明确标识开发阶段。开发计划见根目录详细实施方案及 `docs/PROGRESS.md`。

## 本机运行（Windows PowerShell）

需要 Python 3.11+、Node.js 22+、uv 和 pnpm。首次安装：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.lock.txt
Set-Location apps/web
pnpm install --frozen-lockfile
Set-Location ../..
Copy-Item .env.example .env
.venv/Scripts/python.exe scripts/dev.py
```

打开 http://127.0.0.1:5173 。API 文档在 http://127.0.0.1:8000/docs 。按 Ctrl+C 停止两个服务。若端口已被本项目占用，使用已有进程，不重复启动。

macOS/Linux 的 Python 路径为 `.venv/bin/python`，其余目录相同。系统版本应以实际锁文件和 CI 验证为准。

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
.venv/Scripts/python.exe scripts/create_sample_pdfs.py
```

输出位于 `data/samples/`。这些 PDF 的论文、作者、数字和书目都是**明确标注的虚构软件测试数据**，不是官方赛题素材。`manifest.json` 保存文件哈希和预期事实。通过上传入口选择样例即可验证流程；损坏样例用于异常测试。

已接入用户提供的官方 `pdf/`：完成 Transformer v7、改名 v1、扫描件和 541 页教材的第一轮原生解析测试。v1/v7 去重提示与长书全页处理已经跑通；扫描件仍需 OCR，复杂表格和公式恢复仍待继续开发。完整功能验收不能由原生解析成功替代，实测范围见 `docs/STAGE1_TEST_REPORT.md`。

可以通过 CLI 检查并选择性导入自己的素材，不改变原文件：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/inventory_corpus.py --directory "你的/pdf/目录"
.venv/Scripts/python.exe -X utf8 scripts/import_corpus.py --directory "你的/pdf/目录" --names 1706.03762v7.pdf 1706.03762v1.pdf
```

使用 `--help` 查看完整参数，批量导入全部资料需要显式选择 `--all`。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe scripts/verify_stage1.py
Set-Location apps/web
pnpm build
```

`pytest` 使用隔离临时目录；运行中的本地文献不会被测试覆盖。smoke 脚本连接运行中的 API，并清理本次新建测试文献，保留已有同哈希文献。

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
