# 必要能力收尾：RoBERTa最终配置漏检

客户端日期2026-10-30；应用0.24.2；基线37aa973。[使用说明](QUERY_PRESERVATION.md)。本轮关闭已记录的H01指定证据漏检，未改变预先标注、原文或生成提示。

## 先复现，再修改

在源代码未改的0.24.1基线冻结后，对已有28道开发题做同范围、k=6、模型关闭的HTTP运行：33个指定证据组命中32个，168/168原文锚点有效，0请求错误、0生成请求。冻结文件本身使运行清单的git_dirty为true；基线所列全部产品/评测文件另与37aa973逐字摘要核对一致，不能将其误读为代码已调优。H01“RoBERTa最终配置的masking策略/NSP loss”仍未返回第6页目标段，结果多为第4—5页BERT原设置或消融实验。[原始基线](../output/stage30/baseline) · [基线冻结](../evaluation/freeze_stage30_baseline.json)。

实际问题包括：通用字段补检索丢弃问题里的masking/NSP等词；PDF目标段存在`conﬁguration`连字和`mask-`换行`ing`；中文“最终配置”的意图没有对应词项提示。先尝试仅保留词与断词表示仍未找回目标，后加通用“最终配置/配置/设置/策略”映射才找回。[不足的首次探查](../output/stage30/prototype_tokens.json) · [第二次探查](../output/stage30/prototype_query_concepts.json)。探查均只用本机已准备索引，0生成/下载。

产品改动只有通用词项处理与补检索词保留：最多12个显式英文词、每词64字符，排除否定限制，原章节/阶段与最多12次字段查询仍保留；NFKC及跨行拼接只产生检索词，保留原片段。没有使用论文名、预期答案、页码、块ID或标注来编写检索分支；没有重排模型、额外LLM或改写原文。

## 同一题集对照与首次回答

[检索冻结](../evaluation/freeze_stage30_retrieval_v1.json)后再运行同28题，PDF/解析版本/题集与标注相同：指定证据组由**32/33到33/33**，168/168锚点有效，0错误、0生成请求；没有原先命中组丢失。H01目标现在在6条候选中，仍保留不同设置的原文供核对。[修复后首次运行](../output/stage30/retrieval_first)。这是已观察开发回归的证据组召回，不能称独立测试准确率100%。

[106文件冻结](../evaluation/freeze_stage30_answer_v1.json)在唯一真实生成前保存，覆盖全部应用后端/前端源文件、依赖清单、题集、评测驱动和新测试，并固定RoBERTa文件哈希、确切ParseRun及完整块摘要。仅H01一次DeepSeek compatible/deepseek-flash请求，最多1600输出预留；实际HTTP200，输入1423、输出95 token，usage缺失0，无重试。请求只含原问题、所选范围、6条实际证据和已识别字段，没有标注答案或证据组。

首次回答：“RoBERTa最终配置采用dynamic masking（第4.1节），并使用FULL-SENTENCES格式且不使用NSP loss（第4.2节）。”引用`blk_8720154031034a7b9873d1d9e2514e24`、`parse_086e1c5f3f9b4568a8b25fdc097b4583`的第6页。开发助手逐项核对该原段，三项及章节均一致，未把第4页BERT的NSP设置或第5页消融项混入最终配置。该观察不是独立人工质量签字，semantic_verified仍false。

[首次响应](../output/stage30/live_first/answer.json) · [完整实际载荷/响应](../output/stage30/live_first/calls.json) · [用量汇总](../output/stage30/live_first/summary.json)。保存第一次结果后没有继续修改代码/提示或请求更好的答案。

## 检查与保留的失败

新增10项合成检索检查，最终**623 passed**、1项已有Starlette/httpx弃用警告，201.25秒；TS通过，Vite构建4.41秒。第一次75项定向有2项失败：显式词的Unicode词边界不识别紧贴汉字的QMASK/ZXLOSS。修正为英文标识边界后75项通过，再完成全量623项。[首次失败](../output/stage30/stage30_focused_first.txt) · [修复后定向](../output/stage30/stage30_focused_second.txt) · [最终全量](../output/stage30/stage30_full_final.txt)。临时探查脚本也曾因缺少项目导入路径未执行，修复后再做本机检索；没有模型重试。

新用例覆盖连字、跨行词/软连字符、保留复合词片段、不拼普通换行、正向术语/否定限制、词数上限、通用最终配置提示和锚点原文不变。所有实际评测和新用例都保持原始来源；没有为凑召回率替换标注块。

## 实际界面与归档

打开已保存run `a2d76e0d-70fe-4580-93fd-b3d16b2cc630`，默认模型关闭；候选原文按钮跳到第6页，1个高亮对应确切最终配置段。浏览器0新增模型请求，控制台0错误；390×844窄屏根375/375、回答306/306，无横向溢出。桌面截图同时显示答案、页码6与原文高亮。临时视口恢复、临时标签关闭，用户标签保留。

[桌面](../output/screenshots/stage30-final-configuration-desktop.png) · [窄屏](../output/screenshots/stage30-final-configuration-mobile.png) · [浏览器记录](../output/stage30/browser_checks.json) · [最终核对](../output/stage30/verification_final.json)。核对包含106文件、两次28题的逐题对照、保存响应/来源、14份PDF哈希、库统计、长书5867向量、敏感值与链接。

## 整体状态

已关闭H01这一个明确漏检，且其首次回答与原段相符。M2-QUALITY继续部分实现：该开发题集召回充分不等于所有问答、跨文条件及科研事实质量都已通过；复杂意图/解析及其它必要能力和交付仍需收尾。必要缺口保持19（14项功能部分实现、5项交付未齐），submission_ready=false，不按版本或测试数计算完成百分比。
