# 必要能力收尾：全文计划、分批翻译与编排等待

客户端日期2026-10-30；应用0.24.0；基线214cca7。[操作说明](WORKFLOW_DOCUMENT_TRANSLATION.md)。本轮接通整篇范围及独立预算，没有把原样保留对象算作译文，也没有把软件测试当译文质量评分。

## 实现与验证范围

全文/页范围/指定块清单固定PDF哈希、ParseRun、块ID与指纹。规划0模型请求；批次默认模型关闭，缓存、实际调用与token分别统计。每块检查点、HTTP前预算预留、显式重试、暂停/取消、重启暂停、旧版本、配置/来源变化拒绝、部分导出均已覆盖。参考文献、图表公式等保留；不支持的超长块和无内容页面显式列出。

研究任务到全文步骤先暂停，固定独立翻译对象；完成后明确恢复下游步骤。独立批次不继承编排模型许可，编排0次与翻译55次并列显示。生成结果可按源页定位；校订另存、保留首次候选，不改全局翻译缓存。

新全文测试23项，最终全套**587项通过**，1项已有Starlette/httpx弃用警告；TypeScript通过，最终UI v3生产构建通过（3.19秒）。首次TS检查有JSX括号错误，在冻结与实测前修复，[首次日志](../output/stage28/stage28_tsc_first.txt)保留。[最终测试](../output/stage28/stage28_full_review_final.txt) · [最终构建记录](../output/stage28/build_ui_v3.json)。测试包含模拟HTTP/注入故障，它们不是额外真实请求。

## 冻结、真实调用与首次输出

[v1冻结](../evaluation/freeze_stage28_document_translation_v1.json)在所有真实请求前固定68文件、源文档/块摘要、术语与最多55次新请求；首个失败即停止，不自动重试。沿用translation-v1生成提示与缓存，目标中文，术语`Transfer Learning=迁移学习`。

最初执行申请因自动审批服务的账号使用额度而未运行；用户回复“继续”后，同一命令再次申请并获准，未绕过审批。[记录](../output/stage28/approval_first.txt)。实际只执行一次付费演练。

官方`1910.01108v4.pdf`，5页108块、18417字符；文件SHA-256为`a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6`，ParseRun为`parse_828fd9e7162c4192a1c9ebe64b1ac885`。

| 对象 | 实际结果 |
|---|---|
| 原全文计划670560b3 | 55新译+1缓存+52原样保留，0失败/未支持/待处理 |
| 批次与请求 | 4批包含1次仅缓存检查和3个明确授权的生成批次；55次HTTP均200，无付费重试 |
| 用量 | 输出预留192500；服务商实际输入9708、输出3766 token；未上报usage为0 |
| 编排19555dc6 | 固定原版本，先暂停后继续；证据→全文→写作框架→流程图4步完成，工具尝试5次（全文等待后再确认），编排自身模型0次 |
| 缓存重放e4bc8d38 | 56缓存+52保留，0新请求，未继承原计划的后续校订 |
| 541页长书253ea6cc | 仅规划：12003块、1301591字符，6835待译、5168保留；无解析内容页2/4/10/12，未确认是空白页；0模型调用 |

长书估算6835次新请求、23922500输出token预留，未实际执行翻译，不能声称长书译文验收。上述活动时间不包含用户阅读/等待，编排时间也不包含独立翻译运行，不能合并为端到端性能基准。写作输出是待实施框架，原文指标不是本作品产生的实验结果。

[首次汇总](../output/stage28/live_first/summary.json) · [55份实际载荷/响应](../output/stage28/live_first/calls.json) · [首次全文包](../output/stage28/live_first/paper_complete.zip) · [首次任务包](../output/stage28/live_first/research_complete.zip) · [长书计划](../output/stage28/live_first/longbook_plan.json)。均保留首次输出，没有反复调用挑选较好结果。

## 真实语义错误与另存校订

开发助手逐页查看代表性原文/译文时发现第4页`blk_58fceccf5dc5438eaf9de4362e7abe0d`把损失函数的`term`译成“学期”。原候选：“在损失函数中额外增加一个学期的教师……”。数字保护通过并不能发现这一错误。结合第3页末段`blk_835ed33d9b09485c9f5aab7bc56c4644`，另存候选改为“作为教师，构造损失函数中的一个附加项……”，保留79.8 F1、70.4 EM和不超过3分的条件。

这是开发助手对照原文的1条校订，`origin=assistant_source_review`、`semantic_verified=false`，不是独立人工准确率。首次候选、首次JSON/ZIP和原始缓存均保留；没有改生成提示、重译或新增模型请求。脚注仍可能为普通文本，跨页语境、术语和措辞仍需核对，不能据此认定整篇译文质量通过。

调用后[v2冻结](../evaluation/freeze_stage28_document_translation_review_v2.json)记录4个变更文件，增加来源绑定的校订及固定范围界面；[v3冻结](../evaluation/freeze_stage28_document_translation_ui_v3.json)仅将右侧重复英文原文折叠，后端/提示未再改。两者新增模型调用上限均为0。[校订依据/原候选](../output/stage28/reviewed/source_review.json) · [含校订的全文包](../output/stage28/reviewed/paper_with_review.zip) · [含校订的任务包](../output/stage28/reviewed/research_with_review.zip)。

## 实际界面与归档检查

从实际表单只选DistilBERT、全文和相同术语：预览56缓存/0预计新请求；执行后任务暂停，独立批次默认模型关闭，显式处理1000块上限，实际56缓存+52保留、0新请求；恢复原任务后2/2步完成。任务ID`6065252c-7b99-4197-a236-3ec9a79dc6f5`。[实际UI任务](../output/stage28/ui/task.json) · [UI导出包](../output/stage28/ui/task.zip)。

校订理由为空时保存按钮禁用。第4页原文回跳得到1个选中锚点、对应原块及页码4。1280×720桌面与390×844窄屏检查；窄屏根390/390、对话内容374/374、译文面板315/315，无横向溢出，控制台0错误。未将此有限检查描述成所有复杂版式的双向滚动质量验收。一次结果读取因未限定选择器同时匹配阅读器和对话框而报错；限定范围后读取成功，没有重复执行批次。

[桌面计划](../output/screenshots/stage28-full-plan-desktop.png) · [校订前后](../output/screenshots/stage28-reviewed-translation-desktop.png) · [窄屏](../output/screenshots/stage28-full-translation-mobile.png) · [浏览器记录](../output/stage28/browser_checks.json)。临时视口恢复、临时标签关闭，用户标签保留。

[最终核对](../output/stage28/verification_final.json)包含最终68文件摘要、原PDF/块摘要、库统计、原始候选与校订、缓存、所有本轮ZIP及嵌套ZIP校验、敏感值和链接检查。14文档、13就绪、762页、18733块和长书5867向量保留。

归档核对脚本先后因缓存行字典/字符串比较、预期遗漏`blocked:0`和误以为静默pytest日志含汇总句而停止；分别修正字段读取、清单预期和逐条PASSED计数，没有改产品、缓存或调用模型，也未为改变日志格式重跑测试。[首次记录](../output/stage28/audit_first.txt)。该官方论文此前已用于开发，不能当留出测试样本。

归档还发现68文件清单未包含沿用的`translation.py`适配器，原冻结不补写日期或范围。另在调用后与Git基线214cca7逐字核对该文件，并逐一检查55份实际系统提示/保护后原文载荷；结果一致。[补充核对](../output/stage28/adapter_baseline_check.json)。已通过的调度故障测试含预期应用ERROR日志，归档脚本曾误当失败用例，现按真实测试行区分。

## 整体完成状态

本轮补齐全文计划与编排等待的工程链路。M3-PARALLEL、AGENT-LOOP仍部分实现：完整对齐、译文/研究输出质量与复杂意图等仍待验收。总表保持19个必要缺口（14项功能部分实现、5项交付未齐），submission_ready=false。技术PDF仍旧稿、MP4和公网未完成；下一步按固定收尾计划集中处理必要能力与科研输出质量，不以增加阶段数代替完成。
