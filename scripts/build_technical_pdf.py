"""Rebuild the contest technical draft with ReportLab; use the PDF skill marker first."""
from __future__ import annotations
import argparse
from pathlib import Path
from html import escape
import json

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                               PageBreak, Image, KeepTogether, Flowable)

ROOT=Path(__file__).resolve().parents[1]
PAGES=[
 {'title':'作品概述与交付状态','subtitle':'让研究结论回到具体版本的原文',
  'sections':[
   ('问题与目标','科研阅读往往在上传、问答、比较、引用与写作之间切换。研迹用同一条证据链连接这些操作：文件哈希确定资产，ParseRun保留解析快照，事实引用保存页码、内容块与坐标，任务记录步骤和实际产物。'),
   ('当前作品','已形成React阅读工作台、FastAPI服务、SQLite持久化和受控研究任务。六个科研模块共享文献、版本和来源模型；模型服务不可用时保留原文候选并说明降级。当前应用为0.7.0本地单用户版，本稿对应第八阶段评测与材料整理。'),
   ('本稿性质','这是一份可审阅的参赛技术说明草稿。封面按用户要求暂不填写参赛个人与团队字段；公网部署、视频录制、独立人工质量复核仍未完成。文档不把计划中的完整自主Agent、公式恢复或语义核验写成已实现能力。')],
  'callout':'核心原则：可检查的位置、可保留的缺失、可恢复的过程。'},
 {'title':'需求与六模块映射','subtitle':'功能存在和质量达标分别验收',
  'table':(['赛题工作流','已实现路径','主要边界'],[
   ['文献管理与问答','上传、OCR、版本、固定来源问答','复杂表格/公式；语义支持待复核'],
   ['阅读与联读','目录、数字引用、对比、原译段落','作者年份、词级翻译、译文重排'],
   ['发现与追踪','arXiv/Crossref、确认入库、主题与反馈','最近20条窗口；单机运行依赖'],
   ['图谱与洞察','书目边、局部指标、未来工作候选','外部全文与解决程度未核验'],
   ['写作与绘图','研究计划、证据、CSV六类图、重画包','模板写作；自然语言分析待实现'],
   ['意图任务与交付','五工具计划、预算、检查点和导出','受控状态机；公网鉴权未实现']]),
  'sections':[
   ('验收方式','功能由自动测试、真实PDF接口检查与浏览器操作共同验证。模型事实质量需要固定题集和独立复核；有效坐标与JSON格式本身不能替代它。'),
   ('材料要求','按用户提供文字整理：源码及Git历史、带架构与安装说明的README、5-8分钟且至少1080p的普通话MP4、最多30页的技术PDF、公网演示与必要评审账号。具体阶段和报名模板仍待用户确认。')]},
 {'title':'系统架构与组件取舍','subtitle':'共享证据底座，工具由服务端约束',
  'architecture':True,
  'sections':[
   ('前端与接口','React/TypeScript承载文献、阅读、对比、图谱和任务工作台；PDF.js显示原始页面，内容块坐标支持高亮。FastAPI用显式Schema约束请求，后台处理解析和受控任务，前端轮询展示阶段结果。'),
   ('存储与计算','SQLite适合当前单机部署并保存事务和检查点；原PDF、索引及产物留本地目录。PyMuPDF做原生提取，RapidOCR用于扫描页；BM25与多语言MiniLM通过RRF融合。Matplotlib产生图形和可复现导出。'),
   ('模型接入','服务端适配Chat Completions兼容接口，采用JSON对象输出、来源ID和数字检查。用户本机已接入DeepSeek；选择它证明真实接口闭环，尚无多服务商质量/成本对照，不能声称最优。密钥不进入前端和材料。')]},
 {'title':'数据模型与版本证据链','subtitle':'重新解析后，旧答案仍指向旧快照',
  'table':(['实体','职责','关键关联'],[
   ['Work / Version','作品身份与论文版本','不同版本不静默覆盖'],
   ['Document / SHA256','实际PDF资产','重复文件复用；明确文件身份'],
   ['ParseRun','一次解析的不可漂移范围','历史内容、页面与Block'],
   ['Block / Evidence','原文、页码、坐标和抽取信息','事实引用固定document + run + block'],
   ['Task / Step / Artifact','计划、检查点与产物','确定产物ID支持复用']]),
  'sections':[
   ('身份与坐标','哈希只证明字节相同，不能独立判定学术作品相同。标题、arXiv标识和内容重合提供版本提示，证据不足时由用户选择。页面坐标和旋转信息随解析保存，阅读器按该快照回跳。'),
   ('来源生命周期','重新解析新增ParseRun，历史任务继续使用原run。删除文献会使相关来源失效，不能让旧缓存看起来仍有可用原文。评测按PDF指纹绑定，坐标/文本改变时明确失败，而不是自动替换标准证据。')]},
 {'title':'解析、阅读与翻译','subtitle':'保留原页，暴露候选质量',
  'sections':[
   ('原生与扫描路径','先提取原生内容块、目录、引用与候选结构；扫描页显式准备本地OCR模型后识别文字并保存行坐标。识别分数只表示模型输出分数，关键数字、公式仍需看原图。官方扫描件已有真实OCR检查，541页长书已做原生解析。'),
   ('表格与数学内容','表格保存行列、表头、空值和单元格坐标，作为未核验候选展示。复杂无框与多层表头仍有拆并列问题；公式尚无完整LaTeX恢复。T5样本暴露上标扁平化：2的35次方被抽取为235；数字一致检查无法发现此类原始抽取错误。'),
   ('联读与翻译','数字引用与References之间可往返；作者年份消歧未完整实现。翻译有段落/页/全文任务接口、术语和保护片段、版本缓存、失败重试与原译导出。已做真实单段小样本；长书翻译质量和版式重排尚未验收。')],
  'image':'docs/screenshots/stage6-source.png','caption':'已验收界面：由来源锚点返回GAT v3第9页并高亮原文。截图来自第六阶段。'},
 {'title':'证据问答与多文比较','subtitle':'检索、生成、来源检查和语义判断分开',
  'sections':[
   ('问答流程','用户选定文献与ParseRun后，从正文候选中检索，排除书目区间；BM25与已准备的Dense索引以RRF融合。每题最多返回所设证据数，模型只接收问题和这些片段。生成陈述携带evidence_ids，缺失项单列；格式或服务失败时回退证据检索。'),
   ('受限检查','系统验证ID存在、来源仍可用及数字来源。它既不是完整事实蕴含判定，也不能纠正PDF已经丢失的上标。模型可以给出有来源但未回答所问的背景，或在给出候选后过度保守地声明同一字段缺失。'),
   ('多文路径','比较工具按文献和维度独立检索，每格保存证据、条件和缺失状态。普通跨文问答仍共享候选名额，容易被长文挤占；本次H11/H12暴露覆盖不足。后续需在新开发集对照逐文配额、章节定位和重排策略，再用新留出集验收。'),
   ('本轮冻结','20题首轮与6题模型样本运行前保存代码/题集哈希。观察后没有修改产品检索或提示词来覆盖原结果；标注块召回是严格坐标块命中，不是语义召回。')]},
 {'title':'意图任务、预算与恢复','subtitle':'受控工具计划与可持久化执行',
  'table':(['工具','实际产物','约束'],[
   ['evidence','固定来源的原文候选','逐文检索、一次有界补检索'],
   ['comparison','逐格证据事实矩阵','维度白名单、条件与空缺'],
   ['graph','引用网络和局部指标','已有书目与缓存注册记录'],
   ['writing','可编辑研究计划与证据','不虚构已完成实验'],
   ['diagram','任务流程图及导出','从受控步骤生成设计图']]),
  'sections':[
   ('计划与预算','规则或模型提出工具/维度计划，服务端校验白名单后执行。限制工具次数、模型次数、输出预留和活跃时间；预算耗尽保留部分产物，用户可调整预算后继续。暂停/取消在步骤边界生效。规划请求和执行请求分别记录。'),
   ('持久化与真实检查','步骤和确定产物ID写SQLite，恢复时复用已完成步骤。第七阶段实际停止并重启服务，首步结果不变、attempts仍为1，继续完成5步；执行中断和关闭队列另有自动测试。当前是自实现状态机，不是完整LangGraph Supervisor，也未实现自由自主重规划。')],
  'callout':'planned -> running -> budget_exhausted / paused -> running -> completed_with_gaps；异常与取消单独保留。'},
 {'title':'发现、追踪与引用图谱','subtitle':'元数据、全文与学术结论保持不同身份',
  'sections':[
   ('发现与反馈','arXiv/Crossref提供官方元数据；精确标识符与标题候选分开。确认身份后仅从受限官方路径下载确切arXiv版本，保存查询、时间与哈希。反馈影响共享词排序与隐藏；推荐质量尚无独立统计。'),
   ('主题追踪','第一次检查建立基线，后续记录新作品、新版本、失败和限流；失败不推进成功记录。周期检查依赖本机服务持续运行，只覆盖有限最近窗口，不能宣称全网漏检率已测。'),
   ('图谱与未来工作','边来自原书目，注册记录仅辅助核对身份。已验收样例为53节点、59边，49外部节点多数只有元数据；局部PageRank不代表全球影响力。未来工作提供原文段落和后续候选，未完成全文解决程度或创新性核验。')],
  'image':'docs/screenshots/stage5-network.png','caption':'已验收图谱界面。图的规模不等于全文阅读数量，指标仅反映所选子图。'},
 {'title':'写作框架与可复现绘图','subtitle':'把来源和变换过程一起交给使用者',
  'sections':[
   ('写作的可检查单位','研究计划区分摘录、待核验事实、分析与计划，保存历史版本及来源状态；注册书目不冒充本地全文。导出Markdown、BibTeX与证据包。框架是辅助组织材料，尚非自动完成实证论文。'),
   ('CSV到图形','检查编码、类型、缺失、前导零与重复记录；显式选择聚合、SD/SEM和归一化。六类图形可导出PNG/SVG/PDF，附原始/处理数据、配置、计算图注与真正可运行的重画脚本。演示CSV为自建测试数据。'),
   ('图形与自主性边界','架构图来自受控配置及Mermaid，不执行任意代码。自然语言自由分析、因果推断和无监督实验仍未实现；图表的统计含义必须由研究者核对。')],
  'image':'docs/screenshots/stage6-chart.png','caption':'已验收绘图界面。示例数据为虚构软件测试数据，不是论文实验结果。'},
 {'title':'固定评测协议','subtitle':'按作品隔离，先冻结，后观察',
  'table':(['集合','来源','问题数 / 标注块组'],[
   ['开发回归','Transformer v7、BERT','8 / 8'],
   ['新增留出','RoBERTa v1、T5 v4','12 / 15'],
   ['题型','18单篇、2跨文；全为可回答题','20 / 23']]),
  'sections':[
   ('冻结与请求隔离','题集保存改写的标准事实、文件SHA256、版本、页码、坐标与原文文本指纹。运行前冻结题集和6个产品/评测文件哈希；请求只发送问题、文献、解析版本和候选数，标准答案不会传给问答接口。每次运行产生独立manifest与报告。'),
   ('金标准成熟度','题目与标准事实由开发助手编制，尚待独立人工复核。每个标注块组目前只有一个严格块，其他位置的等价证据不算命中，因此结果偏保守。公开论文也可能存在于模型训练中，不能称盲测或无训练污染。'),
   ('质量复核','复核表绑定问题与响应哈希，填写真实复核人、完整/部分/错误/拒答及引用支持条数。离线复核不重新调用模型。未复核时完整正确率和语义支持率保留null；纯检索不计拒答。未评测无答案识别。')]},
 {'title':'首轮实测结果与失败','subtitle':'锚点有效，仍然可能漏检和答非所问',
  'table':(['范围','有效锚点','严格标注组召回@6'],[
   ['开发8题','48 / 48','7 / 8 = 87.5%'],
   ['留出12题','72 / 72','5 / 15 = 33.3%'],
   ['总计20题','120 / 120','12 / 23 = 52.2%']]),
  'sections':[
   ('检索条件与延时','RoBERTa使用BM25+Dense+RRF；T5因索引尚未准备，本轮与跨文查询回退BM25。本轮不能当成同配置消融。总集顺序延时p50=0.0730s、p95=2.4290s，n=20；nearest-rank，包含冷热与本地核验，不是并发性能。'),
   ('六题DeepSeek采样','首轮检索后选H01/H02/H06/H07/H11/H12，未作预注册随机抽样。6次返回模型模式，4题有陈述、2题为空；36/36锚点有效，7/7陈述引用有效ID。8个严格标注组仅命中1组。p50=2.1589s、p95=2.7870s，n=6。'),
   ('缺陷与结论','样本出现masking缺失、词表候选与缺失声明冲突、T5基线设置漏答、上标丢失、跨文来源覆盖不足。独立人工复核0/6，不能报告事实正确率，更不能把4题有陈述说成4题正确。下一轮应先修复这些通用问题，再另建留出集。')],
  'callout':'本轮不是通过验收的质量结论；它给出了下一阶段可复核的失败基线。'},
 {'title':'工程验证、运行与部署','subtitle':'已经跑通的路径与尚未完成的生产工作',
  'sections':[
   ('自动与真实验证','前七阶段163项自动检查覆盖解析、版本、引用、比较、任务和恢复。本阶段新增19项评测完整性检查；最终全量182项通过，1条依赖弃用警告，耗时34.39秒。真实服务另验证官方PDF、OCR、来源回跳和DeepSeek小样本，注入模型测试不替代真实质量。'),
   ('本地复现','使用Python3.12的.venv312和requirements.lock.txt；apps/web按pnpm-lock.yaml安装。复制.env.example后在本机配置模型，不把密钥写进文档；scripts/dev.py同时启动服务。旧Anaconda环境有原生DLL兼容问题，本机已改用独立Python环境。'),
   ('部署缺口','当前监听loopback，尚无公网身份认证和用户隔离。公开部署前需要用户/文件权限、上传与速率限制、生产代理与HTTPS、服务端配置、备份、评审账号和预算；没有在本轮将开发服务直接暴露到公网。'),
   ('成本与可复现','6题采样最多6次生成、每次最多1600输出token，总预留9600；这不是账单或实际用量。模型不保证逐字一致，可复现要求固定输入、代码、来源、配置、工具轨迹和原始结果，而非承诺每次回答完全相同。')]},
 {'title':'工程创新与AI协同','subtitle':'用可验证的问题定义创新，不用工具数量代替证据',
  'sections':[
   ('版本感知证据','文件、作品版本、解析快照与引用形成闭环，减少重新解析让历史结论漂移的风险。下一步需要扩大版本差异与删除失效场景，比较无快照设计的实际错误数量。'),
   ('逐格溯源与受控任务','比较按文献/维度组织证据，任务按预算持久化产物，连接阅读、图谱和写作。它们是面向科研流程的工程设计，尚无证据证明原创算法或相对现有产品的全面领先。'),
   ('Prompt策略','问答约束只读来源、保留数字条件、缺失单列；比较补充训练阶段和模型规模；计划只允许白名单建议，服务端校验后执行。阶段7用开发题定位并调整，阶段8冻结后没有按留出结果追改。记录见docs/PROMPT_AUDIT.md及freeze_v1.json。'),
   ('AI协同贡献','Codex开发助手参与需求拆解、代码/测试草稿、排错、材料编写和执行验证；用户提供赛题与官方素材并自行配置模型。标准事实与文档尚为助手草稿，不能虚构已经人工逐行核验。参赛者在正式交付前需理解代码、复核来源并能现场解释失败。')]},
 {'title':'交付清单与下一轮工作','subtitle':'当前是可审阅草稿，最终提交仍有具体缺口',
  'table':(['已有','仍需完成'],[
   ['代码、锁定依赖、环境模板、Git阶段记录','远程仓库评审权限、最终发布版本与新机安装'],
   ['本技术说明草稿、7分钟脚本','实际5-8分钟1080p MP4与成片检查'],
   ['20题草稿与真实运行记录','独立金标准、无答案题、语义支持、消融'],
   ['本机完整演示路径','鉴权/隔离、公网部署、评审账号与可用性']]),
  'sections':[
   ('下一轮优先顺序','先定位中文章节词、索引准备状态、跨文候选配额与上标保真。已观察H题转为回归；新收集作品、等价证据和独立复核用于后续留出。原计划60题以及无答案、矩阵质量、并发与内存测试尚未完成。'),
   ('来源与资料','需求依据：用户提供赛题文字及科研小助手Agent-AI大模型.pdf。实现依据：本仓库backend/app、docs/STAGE1-8_TEST_REPORT.md。新增语料：arxiv.org/abs/1907.11692v1 与 arxiv.org/abs/1910.10683v4。截图来自第5-6阶段真实界面。'),
   ('提交前冻结','README团队信息按报名要求补齐；本稿封面暂不写。仓库、文档、视频、部署需同一版本；核对评审访问、额度和运行时间。按主办方正式规则提交后修改，保留最后产物与哈希备份，不静默替换作品。')]},
]


class Architecture(Flowable):
    def __init__(self,width):
        super().__init__();self.width=width;self.height=183
    def draw(self):
        c=self.canv
        items=[('React / PDF.js','文献 · 原文 · 对比 · 图谱 · 研究任务'),
               ('FastAPI / tools','解析 · 检索 · 模型适配 · 受控状态机'),
               ('SQLite + local assets','作品 / 版本 / ParseRun / Evidence / Artifact')]
        for i,(title,detail) in enumerate(items):
            y=132-i*61;c.setFillColor(colors.HexColor('#edf4f8'));c.setStrokeColor(colors.HexColor('#c5d6e2'))
            c.roundRect(0,y,self.width,47,6,stroke=1,fill=1)
            c.setFillColor(colors.HexColor('#15304b'));c.setFont('CJK-Bold',11);c.drawString(13,y+29,title)
            c.setFont('CJK',9);c.drawString(13,y+11,detail)
            if i<2:
                c.setStrokeColor(colors.HexColor('#168c92'));c.line(self.width/2,y-2,self.width/2,y-12)
                c.line(self.width/2-3,y-9,self.width/2,y-12);c.line(self.width/2+3,y-9,self.width/2,y-12)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--font',default='C:/Windows/Fonts/msyh.ttc')
    parser.add_argument('--bold-font',default='C:/Windows/Fonts/msyhbd.ttc')
    parser.add_argument('--content',type=Path,help='Versioned JSON content; omit to rebuild the historical stage8 draft')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--markdown',type=Path)
    args=parser.parse_args()
    content=json.loads(args.content.read_text(encoding='utf-8')) if args.content else {}
    pages=content.get('pages',PAGES)
    version=content.get('version','0.7.0');stage=content.get('stage','8');date=content.get('date','2026-10-28')
    pdfmetrics.registerFont(TTFont('CJK',args.font,subfontIndex=0))
    pdfmetrics.registerFont(TTFont('CJK-Bold',args.bold_font,subfontIndex=0))
    pdfmetrics.registerFontFamily('CJK',normal='CJK',bold='CJK-Bold',italic='CJK',boldItalic='CJK-Bold')
    navy=colors.HexColor('#15304b');teal=colors.HexColor('#168c92');muted=colors.HexColor('#577085')
    body=ParagraphStyle('Body',fontName='CJK',fontSize=11,leading=18.5,textColor=navy,wordWrap='CJK',spaceAfter=10)
    heading=ParagraphStyle('Heading',parent=body,fontName='CJK-Bold',fontSize=13,leading=19,spaceBefore=10,spaceAfter=5)
    title=ParagraphStyle('Title',parent=body,fontName='CJK-Bold',fontSize=24,leading=33,spaceAfter=10)
    subtitle=ParagraphStyle('Subtitle',parent=body,fontSize=11.2,leading=18,textColor=muted,spaceAfter=18)
    caption=ParagraphStyle('Caption',parent=body,fontSize=8.5,leading=13,textColor=muted,spaceAfter=10)
    cell=ParagraphStyle('Cell',parent=body,fontSize=9,leading=14,spaceAfter=0)
    output=args.output or ROOT/'output/pdf/ScholarTrace_技术说明_草稿.pdf';output.parent.mkdir(parents=True,exist_ok=True)
    width=A4[0]-94
    def paragraph(value,style=body):return Paragraph(escape(value),style)
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor('#d6e2e9'));canvas.line(47,45,A4[0]-47,45)
        canvas.setFont('CJK',8);canvas.setFillColor(muted)
        canvas.drawString(47,29,f'研迹 ScholarTrace | 技术说明草稿 | 应用 {version} / 材料阶段 {stage}')
        canvas.drawRightString(A4[0]-47,29,f'{doc.page:02d} / {len(pages):02d}')
        canvas.setFont('CJK',8);canvas.drawString(47,A4[1]-32,'SCHOLARTRACE / '+date.replace('-','.'))
        canvas.setFillColor(teal);canvas.rect(A4[0]-85,A4[1]-33,38,3,fill=1,stroke=0)
    story=[];markdown=['# 研迹 ScholarTrace 技术说明草稿',f'应用{version}；材料阶段{stage}；{date}；参赛身份信息暂不写。','']
    for index,page in enumerate(pages,1):
        if index==1:
            brand=ParagraphStyle('Brand',parent=title,fontSize=31,leading=41,textColor=navy,spaceAfter=4)
            story.extend([paragraph('研迹 ScholarTrace',brand),paragraph('技术说明草稿',subtitle),Spacer(1,15)])
        story.append(paragraph(f'{index:02d}  /  TECHNICAL NOTE',caption))
        story.append(paragraph(page['title'],title));story.append(paragraph(page['subtitle'],subtitle))
        markdown.extend(['## '+page['title'],page['subtitle'],''])
        if page.get('architecture'):story.extend([Architecture(width),Spacer(1,8)])
        if page.get('table'):
            headers,rows=page['table'];cols=len(headers)
            data=[[paragraph(x,cell) for x in headers]]+[[paragraph(x,cell) for x in row] for row in rows]
            widths=([.24,.36,.40] if cols==3 else [.47,.53])
            table=Table(data,colWidths=[width*x for x in widths],hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcecf1')),
                ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f4f7fa'),colors.white]),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),
                ('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),
                ('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,0),.6,teal)]))
            story.extend([table,Spacer(1,9)])
            markdown.extend(['| '+' | '.join(headers)+' |','|'+'---|'*cols,*['| '+' | '.join(row)+' |' for row in rows],''])
        for label,text in page.get('sections',[]):
            story.extend([KeepTogether([paragraph(label,heading),paragraph(text)])]);markdown.extend(['### '+label,text,''])
        if page.get('image'):
            image=Image(str(ROOT/page['image']));factor=min(width/image.imageWidth,280/image.imageHeight)
            image.drawWidth=image.imageWidth*factor;image.drawHeight=image.imageHeight*factor;image.hAlign='LEFT'
            story.extend([Spacer(1,8),image,Spacer(1,5),paragraph(page['caption'],caption)])
            markdown.extend(['!['+page['caption']+'](../'+page['image']+')',''])
        if page.get('callout'):
            note=Table([[paragraph(page['callout'],body)]],colWidths=[width])
            note.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#e9f5f4')),('BOX',(0,0),(-1,-1),.5,teal),('LEFTPADDING',(0,0),(-1,-1),13),('RIGHTPADDING',(0,0),(-1,-1),13),('TOPPADDING',(0,0),(-1,-1),9)]))
            story.extend([Spacer(1,8),note]);markdown.extend(['> '+page['callout'],''])
        if index<len(pages):story.append(PageBreak())
    doc=SimpleDocTemplate(str(output),pagesize=A4,rightMargin=47,leftMargin=47,topMargin=63,bottomMargin=62,
                          title='研迹 ScholarTrace 技术说明草稿',author='ScholarTrace',pageCompression=1)
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    (args.markdown or ROOT/'docs/TECHNICAL_DESIGN_DRAFT.md').write_text('\n'.join(markdown).rstrip()+'\n',encoding='utf-8')
    print(output)


if __name__=='__main__':main()
