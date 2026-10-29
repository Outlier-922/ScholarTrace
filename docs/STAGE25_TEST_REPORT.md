# 必要能力收尾：任务内文献发现（0.21.0）

客户端日期：2026-10-30（Asia/Shanghai）。工作包2，接通既有arXiv/Crossref检索与受控研究任务。基线`1ac7259`，当前产品0.21.0。

## 实现范围

新增第八种工具`discovery`。任务需明确给定查询、服务、模式、作者/年份条件、候选上限和联网方式，规划前完成校验；模型不能增加缺少明确参数的发现操作。精确标识符只使用相应服务，避免同一DOI重复查询。

每来源独立步骤；新增0—4次书目请求预算，在实际HTTP边界保存预留。缓存不扣联网次数；离线路径还在HTTP边界禁止联网。预算不足保留待执行状态，失败按已有次数上限插入本地缓存替代，原失败/快照保留，后续发现转为缓存。模型额度独立，本轮验证始终0次模型请求。

任务按作者字面条件和严格年份区分候选与未采用原因，保存来源URL、时间、缓存/过期状态。候选不进入当前任务的原文集合或写作证据。新增本地快照读取接口；核对页恢复原条件与候选，不自动再次联网；确认入库后需新建后续任务。导出包新增逐来源JSON/Markdown。

## 软件检查

| 检查 | 结果 |
|---|---|
| 首轮定向 | 90通过，1项既有Starlette/httpx弃用提示，31.61秒 |
| 新增发现检查 | 20项；含参数化范围、缺条件阻止规划、离线拒绝联网、实际HTTP边界预留、预算恢复、严格过滤/未采用、过期快照、失败替代/停止、后续服务转离线、标识符路由、模型约束及上下文隔离 |
| 全量Python | 525通过，1项相同提示，198.49秒 |
| TypeScript/Vite | 通过；实际调用前构建与调用后UI小修构建日志均保留 |

HTTP边界测试使用内存MockTransport，保留真实`_fetch`和ContextVar预留路径；另用故意忽略缓存模式的错误辅助函数，验证离线边界仍能阻止实际请求。失败/限流和过期缓存组合为合成软件检查，没有伪称真实服务故障或研究质量结论。

## 冻结与真实注册服务请求

执行前保存[46文件与官方BERT原文快照](../evaluation/freeze_stage25_workflow_discovery_v1.json)。输入为已有`N19-1423.pdf`、原文件哈希/ParseRun/原文块摘要；公开DOI`10.18653/v1/N19-1423`，方式reference，作者字面Devlin、年份2019，每来源至多3条。provider=both经精确DOI规则只规划Crossref。

真实书目请求上限1，模型上限0。验证进程禁用模型密钥和联系邮箱，只向官方固定服务发送公开DOI；作者/年份条件在本机筛选，未发送PDF正文或认证信息。没有启动应用生命周期或恢复其他任务，没有付费调用或自动请求重试。

| 案例 | 任务ID | 实际结果 | 书目 / 模型请求 |
|---|---|---|---|
| 离线预览 | `bbf7ebed-02d6-4ec0-b2b5-7ed56f334c9b` | 无匹配缓存，候选0 | 0 / 0 |
| 真实注册查询 | `aca7c234-e2fe-4fa9-8806-b4a06e2a5dbd` | Crossref成功，候选1 | 1 / 0 |
| 缓存与写作链 | `2dc042ce-00d2-4021-8390-948f13072124` | 缓存命中，4步本地处理 | 0 / 0 |
| 界面筛选 | `823ffe99-8041-4a30-a1e8-0dbeb68e3225` | 2020条件排除2019记录，保留未采用原因 | 0 / 0 |

前三条活动时间为0.204、2.265、1.686秒；界面任务显示4.2秒，包含本机默认检索路径，不能作为性能基准。所有任务为`completed_with_gaps`。原来源与预算不扩大，任务包CRC及`task.json`一致性通过。

Crossref返回标题“BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding”，DOI规范为小写，作者Jacob Devlin、Ming-Wei Chang、Kenton Lee、Kristina Toutanova，年份2019；精确标识符、作者和年份检查均真。原始响应和首次请求参数保存在`registry_records.json`，不记录请求头。[注册来源](https://api.crossref.org/works/10.18653%2Fv1%2Fn19-1423)。这验证书目身份条件，没有验证全文、引用价值或科研结论。

结果见[实际摘要](../output/stage25/registry_first/summary.json)。脚本`scripts/verify_workflow_discovery.py`拒绝覆盖输出，限制一次注册服务请求，缓存流程0次联网；无需为重演删除真实缓存。

## 浏览器验收与UI补充快照

实际查看真实候选、作者年份理由及1/1书目、0/0模型计数。点击交接后，核对页恢复原DOI、Crossref、reference、Devlin、2019和同一候选，并显示已读取本地快照。没有点击再次检索、外部全文或导入，也没有新增论文。

真实表单在缺少查询时禁用规划；选择原BERT上下文和同一DOI、参考文献模式、作者Devlin、年份2020、只读缓存，先预览再运行。结果0条通过、1条未采用，保留“年份不匹配或缺失”，新增书目/模型请求均0。手机任务页根宽390、scrollWidth390，内容宽374、scrollWidth374；核对页根client/scrollWidth375，弹窗client/scrollWidth329，均无横向溢出。视口已恢复、临时测试标签关闭，浏览器错误日志为空。

交接的短暂加载阶段原先沿用“查询官方元数据”提示，容易误解为重新联网；调用后仅调整`LiteraturePanel.tsx`区分“读取本地快照”和独立手动查询，另存[UI v2快照](../evaluation/freeze_stage25_workflow_discovery_ui_v2.json)。后端、请求逻辑、原文与提示未变。随后候选说明再改为“书目快照，全文需单独核对”，避免误判本机是否已有PDF，另存[最终UI v3](../evaluation/freeze_stage25_workflow_discovery_ui_v3.json)，仅TaskDiscovery.tsx措辞变化。两次UI修正后均0次新增注册服务或模型调用。

最终[候选截图](../output/screenshots/stage25-discovery-ui-v3.png)及[UI v3记录](../output/stage25/browser_checks_ui_v3.json)。首轮截图：[真实候选](../output/screenshots/stage25-discovery-desktop-final.png)、[快照交接](../output/screenshots/stage25-snapshot-handoff.png)、[手机](../output/screenshots/stage25-discovery-mobile.png)、[真实筛选](../output/screenshots/stage25-excluded-candidate.png)。[浏览器记录](../output/stage25/browser_checks.json)及[最终UI v2](../output/stage25/browser_checks_ui_v2.json)。最初桌面截图因滚动位置只显示卡片下部，单独保留，未替换或删除首次结果。

## 最终一致性检查

[最终记录](../output/stage25/verification_final.json)确认最终46份冻结文件一致，现存BERT原PDF字节哈希与原文块摘要匹配，三条脚本任务及本地检索快照与HTTP结果相同。界面筛选任务另存JSON/ZIP；4份ZIP、30个解压成员通过CRC和私密值检查，未发现本机`.env`私密值。原库14篇、13篇就绪、762页、18733块及5867条长书向量保留；API/前端0.21.0可访问，文档相对链接有效。

## 验收判断

AGENT-LOOP/M4-DISCOVERY保持部分实现：本轮完成明确条件书目发现、预算/反馈与核对入口交接，没有实现批量自动下钻/下载编排、自动扩展全文来源或引用价值评测。元数据始终不当全文证据，写作和绘图继续使用原先选定的本地论文。

整体仍14项必要能力部分实现、5项交付未齐，`submission_ready=false`。CSV/全文翻译等编排与完整科研质量、最终技术PDF/视频、公网和仓库评审访问及参赛身份仍按[固定完成关口](DELIVERY_PLAN.md)收尾，没有因为新增工具或525项检查改写完成标准。
