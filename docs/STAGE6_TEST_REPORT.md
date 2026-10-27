# 第六阶段验收：写作框架与科学绘图

日期：2026-10-27。版本：0.6.0。本阶段为本机单用户开发版。

## 已交付

“写作与绘图”入口现在有三个可操作页签：

1. **写作框架**：输入想法，选择本地文献或已有图谱，组织摘要、研究问题、相关工作、方法、实验计划和局限。每篇最多取两段正文，固定到文献、ParseRun、块 ID、页码、坐标和原文；过滤标题、书目和过短碎片。缺少适合正文时显示待补充计划，不生成引文。
2. **版本与书目**：段落区分原文摘录、待核验事实、用户分析和未实施计划。摘录需逐字对应一个已选证据；事实需有效来源且不能增加来源没有的数字。保存检查版本冲突，历史只读；删除来源后关联草稿及历史失效。注册身份与标题相符才对应书目，标题候选还需核对作者。未对应文件单列本地来源，不填造 DOI、作者、年份或期刊。支持 Markdown、BibTeX、证据 ZIP。
3. **CSV 科学绘图**：编码、类型、缺失、重复、前导零及原始预览；六个模板：数据表、折线、柱状、雷达、分层三维雷达、三维散点。明确选择单位、分组、重复标识、聚合和清洗规则。输出 PNG/SVG/PDF、图注和复现 ZIP。
4. **架构图**：受控节点与连接、方向箭头、DAG/环形布局，导出 PNG/SVG/PDF/Mermaid 和重画脚本。明确标注为设计草图，待实施验证。

## 数据与证据规则

- CSV 支持 UTF-8/BOM、UTF-16/BOM、GB18030，逗号/分号/制表符；限制 2 MB、10,000 条记录、64 列。
- 缺失默认报错；明确选择删除才排除，保留记录序号。折线使用 NaN 断开缺失位置，避免跨缺失观测连线。
- 同一横轴/分组的多观测必须明确聚合才能用于单值折线、柱状或雷达；重复实验 ID 不得重复。无独立性证明时不声称独立实验。
- SD 为样本标准差（ddof=1），SEM 为 SD/√n；每条误差条至少两次不同标识的观测。不称为置信区间或显著性检验。
- 雷达逐指标声明越大/越小越好，选 min-max 或明确边界；不自动推断方向、不裁剪越界值。常量指标记为 0.5，并提示不能据此排名。三维雷达 z 轴只是类别分层；三维散点使用三个实际列。
- 图注仅陈述计算得到的范围、记录使用与规则，不推导因果、显著性或实验已实施。演示与重画均有自建数据标识。
- ZIP 保留原始 CSV、SHA-256、处理后 CSV、配置、字体/Python/Matplotlib 环境、图注、同一份重画脚本及依赖。处理后 CSV 将可能触发公式的文本加文本前缀，真实数值保持数值；不执行上传代码。

## 自动验证

~~~text
142 passed, 1 warning in 15.53s
TypeScript build: passed
Vite production build: passed, 1594 modules transformed
~~~

新增 34 项检查，覆盖三类编码、错行/引号/控制字节、非有限数值、明确删除、前导零、均值/SD/SEM、重复 ID、归一化边界/方向/常量、数值横轴别名、图像实际生成、PDF 文字、公式文本保护、输入篡改、原文引用、错误数字、外来引用、历史版本、版本冲突、来源删除与书目字段导出。

测试 PDF 和注册记录均为虚构软件夹具，不作为学术事实。唯一警告来自现有 Starlette 测试客户端的 httpx 适配弃用提示，未影响测试。

## 真实论文 HTTP 验收

运行 scripts/verify_stage6.py，使用库内官方 Transformer v7、BERT、GPT-1、GAT v3。

| 检查 | 最终结果 |
|---|---|
| 来源 | 4 个作品 |
| 正文证据 | 8 段，逐项核对原 ParseRun 的文字与坐标 |
| 注册书目 | 2 条（Transformer、GAT） |
| 待核对本地来源 | 2 篇（BERT、GPT-1），明确保留状态 |
| 草稿历史 | 创建 v1、编辑为 v2，v1 仍可读 |
| CSV | 9 条自建演示观测：3 个虚构模型 × 3 次编号 |
| 图表 | 六种模板的 PNG/SVG/PDF 和完整 ZIP |
| 重画 | 实际运行导出脚本，处理后 CSV 与图注逐字节一致 |
| 输入被改动 | 自动测试确认拒绝用旧哈希静默重画 |
| 最终 HTTP 脚本耗时 | 7.093 秒；功能冒烟检查，非通用性能基准 |
| 生成模型 | 未参与 |

CSV SHA-256：c97768596cb5841657e492d9907d57d91031937e9f5b9b55000243ec796620bc。

最终脚本草稿 ID：e8a30914-77af-4b87-98a4-b5b47df06eba。报告、图表、PDF 渲染预览和导出包位于本机 data/stage6_verification/；数据目录按项目约定不进入 Git。

## 浏览器与视觉验收

- 桌面 1440×1050：实际载入演示 CSV，设置 accuracy 的 % 单位和 SD，生成柱状图、图注与下载入口。
- 人工验收草稿 dd71e6a3-9d18-4c6f-a8b7-47c139ba3e4f：查看 v1 只读，返回 v2；核对 BERT 标题及四位作者后对应已有 Crossref DOI，保存 v3；编辑未实施的方法计划，保存 v4。此记录含三条对应书目和一篇待核对本地来源，与最终 HTTP 脚本的两条自动对应分别报告。
- 从 GAT 未来工作摘录点击“PDF 第 9 页”，阅读器回到第 9 页并定位原内容块。
- 五节点架构图实际生成，方向箭头可见，带“设计草图，待实施验证”标识。
- 窄屏 390×844：写作、架构图、历史图表与下载可操作；锁定背景滚动，内容宽度不超过视口。浏览器无 warning/error。
- 六种图表 PDF 均渲染检查：中文、图例、误差条、方向与演示标识可见。三维雷达的极端归一化可能使对象退化为线段，来自数据本身。

截图：[写作](screenshots/stage6-writing.png)、[图表](screenshots/stage6-chart.png)、[架构图](screenshots/stage6-diagram.png)、[原文回跳](screenshots/stage6-source.png)、[窄屏写作](screenshots/stage6-mobile.png)、[窄屏图表](screenshots/stage6-mobile-chart.png)、[窄屏架构](screenshots/stage6-mobile-diagram.png)。

## 边界与下一阶段

当前写作是固定模板下的证据组织和研究计划，尚未用真实模型生成并评测完整论文、语义综述或创新性。来源/数字检查不能证明语义支持；用户分析标为未核验。

CSV 尚未接入自然语言自主选图、自由 Python 分析、多表连接、显著性检验或自动实验。用户尚未提供真实实验 CSV，演示数值全部为自建数据。跨系统重画需要可用中文字体；字体可能改变布局，不承诺图像跨系统逐字节相同。

下一步：接入真实模型，验收问答、对比、翻译质量，再完善意图驱动编排、比赛场景评测、部署和提交材料。

## 复现

~~~powershell
.venv312/Scripts/python.exe -m pytest -ra
.venv312/Scripts/python.exe scripts/dev.py
# 另开终端，确保四篇官方论文已入库
.venv312/Scripts/python.exe -X utf8 scripts/verify_stage6.py
~~~

打开“写作与绘图”。CSV 可选 examples/synthetic_plotting_demo.csv，或点“加载自建演示 CSV”。ZIP 解压安装依赖后运行 python render.py，输出在 reproduced/。

实现参考 Matplotlib 官方的 [雷达示例](https://matplotlib.org/stable/gallery/specialty_plots/radar_chart.html)、[误差条接口](https://matplotlib.org/stable/api/_as_gen/matplotlib.axes.Axes.errorbar.html) 与 [图像导出接口](https://matplotlib.org/stable/api/_as_gen/matplotlib.figure.Figure.savefig.html)。
