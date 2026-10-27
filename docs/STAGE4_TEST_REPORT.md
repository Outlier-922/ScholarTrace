# 第四阶段实测记录（0.4.0）

日期：2026-10-27（北京时间）。本阶段在原有 Python 3.12 / React 工作区继续开发，没有部署公网服务，也没有配置或使用真实生成模型。

## 已实现

- 发现与追踪入口：支持标题、参考文献片段、研究主题、DOI 和 arXiv 标识符。References 列表可把原始条目带入检索框。
- arXiv Atom 与 Crossref JSON 适配；标识符查询只接受对应记录，带版本的 arXiv 查询不会用其他版本代替。作者/年份核对结果单独展示，不能把首条标题候选当成已确认身份。
- 元数据保存来源 URL、抓取时间、查询参数与结果；六小时缓存。缓存命中后重新应用当前反馈，不把旧推荐状态固定在缓存里。
- arXiv 具体版本 PDF 下载 → 现有上传/哈希去重/后台解析流水线。保存注册记录、下载 URL、版本、时间、文件 SHA256 和文献 ID 的关联。
- Crossref 记录保持“仅元数据”；不把返回的任意 `link` 当成可下载开放全文，不绕过出版方权限。
- 关键词与显式点赞文献的共享词排序；逐项展示真实理由。点踩后默认隐藏，可以显示并撤销。没有宣称神经推荐、代码复现或引用价值评估。
- 持久化主题、已见作品/版本、成功时间和每次检查记录。首次成功只建立基线，后续区分新作品、新版本、无变化、失败与限流。
- 本机后台调度只检查启用且到期的主题；成功后下次为 24 小时，失败后为 1 小时。服务关闭时不运行，重启后检查到期任务。本轮演示主题自动检查始终关闭。
- 修复阅读器对新导入文献过早获取引用映射的问题：当前解析未完成时等待，完成状态变化后重新获取；读取历史 ParseRun 仍可独立请求。

## 来源与请求边界

依据 [arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)、[API 使用说明](https://info.arxiv.org/help/api/tou.html) 和 [Crossref 官方接入文档](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) 实现。

- 元数据固定请求官方 HTTPS 端点，不跟随重定向，正文上限 4 MB。
- arXiv 请求串行、至少间隔 3.05 秒；元数据限流后遵守有界等待。Crossref 可选真实联系邮箱由环境变量配置，不编造联系方式。
- 下载仅允许 arxiv.org / export.arxiv.org、标准 HTTPS 端口和对应版本的 PDF 路径。拒绝换主机、换论文 ID、携带查询参数的重定向；最多三次跳转，大小受上传预算限制，并校验 PDF 文件头。
- 元数据不进入原文问答范围，只有成功下载并解析的文献才成为证据来源。没有发送 PDF 正文到元数据服务。

## 自动化与构建

**88 项测试通过**：原 70 项 + 本阶段 18 项。

新增测试涵盖标识符解析、标题短语查询回归、Atom/JSON 记录规范化、XML 实体拒绝、Crossref 不可信全文链接排除、缓存与反馈重应用、作者/年份不匹配展示、错版本排除、请求失败/限流区分、确认后导入、哈希去重、来源绑定、跨主机/换 ID 重定向拒绝、追踪基线/版本增量/失败不推进水位，以及只调度启用且到期的主题。

网络协议测试使用明确的虚构元数据和注入响应；没有将其作为论文内容或真实 API 效果。TypeScript 检查、Vite 生产构建及 Python 编译检查通过。测试仍有一条既有 Starlette/httpx 弃用提醒。

## 真实 HTTP 与文件验证

`scripts/verify_stage4.py --import-arxiv 1710.10903v3 --check-topic "graph attention networks"` 的结果保存在本地 `data/stage4_official_report.json`（忽略 Git）。

| 操作 | 实际结果 |
|---|---|
| arXiv `1706.03762v7` | 返回 Attention Is All You Need，2017，具体 v7 |
| 标题 Attention Is All You Need | arXiv 短语查询返回原作及标题变体；Crossref 返回同名不同年份候选，未自动确认 |
| DOI `10.18653/v1/N19-1423` | Crossref 返回 BERT，2019，4 位作者；保持仅元数据 |
| arXiv `1710.10903` | 返回 Graph Attention Networks 的 v3；作者 Petar Veličković 等，2017 |
| Graph Attention Networks 下载 | 实际新加入 12 页 PDF，271 个内容块，解析 ready |
| 相同版本再次下载 | `upload_outcome=reused`，文献 ID 不变，下载文件 SHA256 与来源记录一致 |
| 文件 SHA256 | `a7d811a44e1c65bf27da3a01859f23a8edbe33b8f96114b4b5066aedb078f284` |
| 主题首次手动检查 | 建立 20 条基线，不标成新增 |
| 紧接的第二次检查 | 窗口内无变化，新增数 0 |

新增真实论文保存在本机数据目录，原官方赛题文件未改变。文献库现有 10 个 PDF、629 页（包含原有明确标注的几何测试文件）。实际 API 和网络状态可能随时间变化，复现脚本不承诺特定排序或数量永久一致。

## 浏览器验收

- 精确查询 Graph Attention Networks，填写 Petar / 2017，看到作者、年份匹配与具体 v3。
- 未勾选“已核对标题、作者与版本”前，入库按钮禁用；勾选后实际下载入库并通过后台解析。
- 点赞及再次点击撤销，`aria-pressed` 与显示状态一致，最终还原中性反馈。
- 主题页显示自动检查暂停，查看到“首次 20 条基线”及“第二次无变化”的真实记录。
- 从 References 的原始片段进入发现界面，查询框保留文本，检索模式为 `reference`。
- 桌面 1440×1000 和窄屏 390×844 检查；窄屏弹窗位于可视范围，文档宽度未溢出。
- 修复引用刷新时一次开发热更新提示了 effect 依赖数组长度变化，整页重载后消除；不是线上运行结果。重载后新论文的 84 个书目候选片段可显示。随后创建新 ParseRun `parse_3940b52cbe9c48a29ed4cac4fed87712` 重新解析，不重载页面即恢复 84 个候选，旧解析保留。这些是原解析片段，不能当作 84 篇已核实文献，续行归并仍待完善。

截图：[发现与入库](screenshots/stage4-discovery.png)、[主题检查](screenshots/stage4-tracking.png)。

## 当前限制与下一步

- 追踪只检查最近更新的最多 20 条，可能漏掉窗口之外的论文，不是全量收割；没有实等 24 小时，周期逻辑以调度测试验证，真实 API 以两次手动检查验证。
- 推荐是关键词和显式反馈的可解释基线，尚无经过评测的向量兴趣模型、主题多样性或复现准备度信号。作者年份格式书目关系与跨来源身份合并仍需进一步消歧。
- PDF 自动获取仅覆盖 arXiv；其他来源需用户核对开放获取权限后手动上传。未集成 Unpaywall、Semantic Scholar、PubMed 或任意网址下载。
- 单用户 SQLite / 单 API 进程调度版；多实例任务租约、公网鉴权、用户隔离、全量增量收割和跨设备通知未完成。
- 下一阶段是 20 篇以上有出处的引用网络、作品/版本归并、局部核心节点与综述证据链。真实生成模型翻译/问答仍需本机配置密钥后验收。

## 复现

```powershell
.venv312/Scripts/python.exe -m pytest -q
.venv312/Scripts/python.exe scripts/dev.py
# 另开终端，默认做两次标识符检索及一次标题检索
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage4.py
# 显式选择下载与暂停主题的两次检查
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage4.py --import-arxiv 1710.10903v3 --check-topic "graph attention networks"
```
