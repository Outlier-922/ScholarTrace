"""Evidence-bound writing plans and controlled scientific chart artifacts."""
from __future__ import annotations
import copy
import hashlib
import io
import json
import platform
import re
import shutil
import threading
import uuid
import warnings
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter,File,HTTPException,Query,UploadFile
from fastapi.responses import FileResponse,PlainTextResponse,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from . import graph,literature,literature_store,plot_runtime as runtime,qa,store,studio_store as repo

router=APIRouter(prefix='/api/studio',tags=['writing-and-plotting'])
_render_lock=threading.RLock()
FILES={'figure.png','figure.svg','figure.pdf','manifest.json','caption.txt','processed.csv','render.py','requirements.txt','README.txt','architecture.mmd'}


class ChartSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    chart_type:Literal['table','line','bar','radar','radar3d','scatter3d']='bar'
    x:str=Field(max_length=100)
    y:list[str]=Field(min_length=1,max_length=8)
    group_by:str|None=None
    repetition:str|None=None
    x_kind:Literal['categorical','numeric']='categorical'
    aggregation:Literal['none','mean']='none'
    missing_policy:Literal['report','drop']='report'
    error_bar:Literal['none','std','sem']='none'
    normalization:Literal['none','minmax','bounds']='none'
    directions:dict[str,Literal['higher','lower']]=Field(default_factory=dict)
    bounds:dict[str,list[float]]=Field(default_factory=dict)
    units:dict[str,str]=Field(default_factory=dict)
    title:str=Field(default='数据可视化',min_length=1,max_length=150)
    x_label:str=Field(default='',max_length=100)
    y_label:str=Field(default='',max_length=100)

    @model_validator(mode='after')
    def bounded_labels(self):
        if any(len(key)>100 or len(value)>50 for key,value in self.units.items()) or len(self.units)>64 or len(self.bounds)>64 or len(self.directions)>64:
            raise ValueError('单位与归一化配置过长')
        return self


class ChartRequest(BaseModel):
    dataset_id:str
    spec:ChartSpec


class DiagramNode(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,30}$')
    label:str=Field(min_length=1,max_length=60)


class DiagramEdge(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str=Field(max_length=31)
    target:str=Field(max_length=31)


class DiagramRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(default='研究方法流程草图',max_length=150)
    nodes:list[DiagramNode]=Field(min_length=2,max_length=15)
    edges:list[DiagramEdge]=Field(min_length=1,max_length=35)


def asset(identity,kind=None,revision=None):
    payload=repo.get(identity,revision)
    if not payload or (kind and payload['kind']!=kind):raise HTTPException(404,'产物或其来源不可用')
    return payload


def create_dataset(raw,filename,data_status='user_data'):
    try:parsed=runtime.parse_csv(raw)
    except ValueError as error:raise HTTPException(422,str(error))
    identity=str(uuid.uuid4());folder=repo.folder(identity);folder.mkdir()
    (folder/'input.csv').write_bytes(raw)
    payload={key:value for key,value in parsed.items() if key!='rows'}
    payload.update(id=identity,kind='dataset',created_at=store.now(),title=filename,data_status=data_status,preview=parsed['rows'][:15])
    repo.create(payload);return payload


@router.post('/datasets')
async def upload_csv(file:UploadFile=File(...)):
    try:
        if not (file.filename or '').lower().endswith('.csv'):raise HTTPException(422,'目前仅接受CSV文件')
        raw=await file.read(runtime.MAX_BYTES+1)
        return create_dataset(raw,Path((file.filename or 'data.csv').replace('\\','/')).name[:150])
    finally:await file.close()


def demo_bytes():
    # Intentionally fictional; never report these as benchmark or experimental results.
    rows=['model,run,accuracy,latency_ms,parameters_million,energy_j']
    for index,model in enumerate(['演示模型A','演示模型B','演示模型C']):
        for run in range(1,4):rows.append(f'{model},{run},{72+index*4+(run-2)*.7},{18+index*5+(run-2)*.6},{10+index*6},{3+index*.8+(run-2)*.1}')
    return ('\n'.join(rows)+'\n').encode('utf-8-sig')


@router.post('/datasets/demo')
def demo():return create_dataset(demo_bytes(),'synthetic_plotting_demo.csv','synthetic_demo')


@router.get('/datasets/{identity}')
def dataset(identity:str):return asset(identity,'dataset')


def read_dataset(identity):
    payload=asset(identity,'dataset');raw=(repo.folder(identity)/'input.csv').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=payload['sha256']:raise HTTPException(409,'原始CSV哈希已改变，请重新导入，不会继续使用旧配置')
    parsed=runtime.parse_csv(raw);parsed['data_status']=payload['data_status']
    return payload,raw,parsed


def render_asset(kind,spec,dataset_id=None):
    identity=str(uuid.uuid4());folder=repo.folder(identity);folder.mkdir()
    try:
        data=None
        if dataset_id:
            profile,raw,parsed=read_dataset(dataset_id);prepared=runtime.prepare_chart(parsed,spec)
            (folder/'input.csv').write_bytes(raw)
            data={'dataset_id':dataset_id,'sha256':parsed['sha256'],'data_status':profile['data_status'],'used_records':prepared['used_records'],
                  'dropped_rows':prepared['dropped_rows'],'statistics':prepared['statistics'],'normalizations':prepared['normalizations'],'warnings':prepared['warnings'],'caption':prepared['caption']}
        with _render_lock:
            with warnings.catch_warnings(record=True) as observed:
                warnings.simplefilter('always')
                try:
                    environment=runtime.render_chart(prepared,folder) if kind=='chart' else runtime.render_diagram(spec,folder)
                    if any('Glyph' in str(item.message) and 'missing' in str(item.message) for item in observed):raise ValueError('当前字体缺少所需字符，请使用已安装的中文字体后重画')
                finally:
                    import matplotlib.pyplot as plt
                    plt.close('all')
        environment['python']=platform.python_version()
        manifest={'id':identity,'kind':kind,'created_at':store.now(),'title':spec['title'],'spec':spec,'template_version':'studio-templates-v1',
                  'environment':environment,**(data or {}),'warnings':(data or {}).get('warnings',[])}
        if kind=='chart':
            runtime.write_processed(prepared,folder/'processed.csv')
            (folder/'caption.txt').write_text(prepared['caption'],encoding='utf-8')
        else:
            manifest['caption']='设计草图，展示用户声明的模块与连接，不代表系统已实现或经过实验验证。'
            (folder/'caption.txt').write_text(manifest['caption'],encoding='utf-8')
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        shutil.copyfile(Path(runtime.__file__),folder/'render.py')
        (folder/'requirements.txt').write_text('matplotlib==3.11.2\nnumpy==2.5.4\n',encoding='utf-8')
        (folder/'README.txt').write_text('运行：python render.py\n输入是数据，不执行上传代码。原CSV保留原样；processed.csv将危险公式前缀作为文本导出。\n重新生成到reproduced/，不会覆盖原图。manifest记录单位、清洗、归一化和字体。跨系统字体变化可能影响外观，不改变统计。\n自建演示数据不是论文实验；图注不证明因果、显著性或实验实施。\n',encoding='utf-8')
        repo.create(manifest);return manifest
    except BaseException:
        root=(store.data_dir()/'studio').resolve()
        if folder.resolve().is_relative_to(root):shutil.rmtree(folder,ignore_errors=True)
        raise


@router.post('/charts')
def create_chart(body:ChartRequest):
    if len(set(body.spec.y))!=len(body.spec.y):raise HTTPException(422,'指标列不可重复')
    try:return render_asset('chart',body.spec.model_dump(),body.dataset_id)
    except ValueError as error:raise HTTPException(422,str(error))


@router.post('/diagrams')
def create_diagram(body:DiagramRequest):
    spec=body.model_dump()
    if len({node['id'] for node in spec['nodes']})!=len(spec['nodes']):raise HTTPException(422,'节点ID必须唯一')
    if any(edge['source']==edge['target'] for edge in spec['edges']):raise HTTPException(422,'请用独立步骤表达反馈，不支持自环')
    if len({(edge['source'],edge['target']) for edge in spec['edges']})!=len(spec['edges']):raise HTTPException(422,'边不可重复')
    try:return render_asset('diagram',spec)
    except ValueError as error:raise HTTPException(422,str(error))


@router.get('/assets')
def assets(kind:Literal['dataset','chart','diagram','draft']|None=None):return {'items':repo.list_assets(kind)}


@router.get('/assets/{identity}')
def get_asset(identity:str):return asset(identity)


@router.get('/assets/{identity}/files/{name}')
def artifact_file(identity:str,name:str):
    payload=asset(identity)
    if payload['kind'] not in {'chart','diagram'} or name not in FILES:raise HTTPException(404,'文件不存在')
    path=repo.folder(identity)/name
    if not path.is_file():raise HTTPException(404,'文件不存在')
    media={'svg':'image/svg+xml','png':'image/png','pdf':'application/pdf'}.get(path.suffix[1:],'text/plain')
    return FileResponse(path,media_type=media,filename=name,content_disposition_type='inline' if media.startswith('image/') else 'attachment',
                        headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'",'X-Content-Type-Options':'nosniff'})


@router.get('/assets/{identity}/bundle')
def bundle(identity:str):
    payload=asset(identity)
    if payload['kind'] not in {'chart','diagram'}:raise HTTPException(422,'请使用对应草稿导出入口')
    stream=io.BytesIO();folder=repo.folder(identity)
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES|{'input.csv'}):
            path=folder/name
            if path.is_file():archive.writestr(name,path.read_bytes())
    return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{payload["kind"]}-reproduction.zip"'})


class DraftRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    idea:str=Field(min_length=4,max_length=4000)
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    graph_id:str|None=None


def reference_for_document(document,records):
    key=graph.doc_key(document)
    index=graph.registry_index(records)
    if key in index and graph.norm(index[key]['title'])==graph.norm(document['title']):return {'document_id':document['id'],'metadata':index[key],'identity_basis':'identifier_and_title','status':'registry_identity'}
    candidates=[record for record in records if graph.norm(record['title'])==graph.norm(document['title'])]
    return {'document_id':document['id'],'metadata':None,'identity_basis':'local_file_only','status':'bibliography_pending','candidates':[record['key'] for record in candidates]}


def build_draft(body):
    if body.graph_id:
        snapshot=graph.graph(body.graph_id);sources=snapshot['sources']
    else:
        docs=[store.get_document(identity) for identity in dict.fromkeys(body.document_ids)]
        if not docs or any(document is None for document in docs):raise HTTPException(422,'请选择至少一篇可用文献或已有图谱')
        if any(document['status'] not in {'ready','partial'} for document in docs):raise HTTPException(409,'请先等待解析完成')
        sources=[{'document_id':item['document']['id'],'parse_run_id':item['document']['parse_run_id'],'title':item['document']['title'],'version_label':item['document'].get('version_label')} for item in graph.choose_sources(docs)]
    records=graph.cached_records();references=[];evidence=[]
    for source in sources:
        document=store.get_document(source['document_id'])
        if not document:raise HTTPException(404,'来源已删除')
        references.append(reference_for_document(document,records))
        structure=store.get_structure(document['id'],parse_run_id=source['parse_run_id'])
        if not structure:raise HTTPException(409,'来源解析不可用')
        blocks={block['id']:block for block in structure['blocks']}
        reference_ids={reference['block_id'] for reference in structure['references']}
        reference_ids|={anchor['block_id'] for item in graph.bibliography_units(structure) for anchor in item['sources']}
        candidates=qa._retrieve(body.idea+' method architecture limitations',[{**document,'_retrieval_run':source['parse_run_id']}],24)
        evidence += [item for item in candidates if item['block_id'] in blocks and item['block_id'] not in reference_ids
                     and blocks[item['block_id']]['type']=='paragraph' and len(item['text'].split())>=25][:2]
    def paragraph(kind,text,ids=None):return {'id':str(uuid.uuid4()),'kind':kind,'text':text,'evidence_ids':ids or [],'review_status':'planned' if kind=='plan' else 'source_excerpt'}
    sections=[{'id':'abstract','title':'摘要（研究计划草案）','paragraphs':[paragraph('plan','用户研究想法（尚未验证）：'+body.idea),paragraph('plan','拟明确研究问题、方法与验证条件；实验尚未实施，不填写未经取得的结果。')]},
              {'id':'introduction','title':'引言与研究问题','paragraphs':[paragraph('plan','待补充：研究对象、适用条件和待验证假设。不能把本次检索未找到的内容直接称为研究空白。')]},
              {'id':'related','title':'相关工作：原文证据','paragraphs':[paragraph('quote',item['text'],[item['id']]) for item in evidence]},
              {'id':'method','title':'方法设计（待实施）','paragraphs':[paragraph('plan','待补充：输入与输出、模块连接、关键假设、计算与资源预算，以及与基线的区别。')]},
              {'id':'experiments','title':'实验计划','paragraphs':[paragraph('plan','待补充：数据来源与许可、训练/测试划分、指标方向、比较基线、重复实验标识、消融与失败判据。未完成前不写准确率提升或显著性结论。')]},
              {'id':'limitations','title':'局限与可证伪条件','paragraphs':[paragraph('plan','待补充：可能失败的场景、数据偏差、外推边界与最小验证实验。结论需与取得的证据一致。')]}]
    now=store.now();payload={'id':str(uuid.uuid4()),'kind':'draft','created_at':now,'updated_at':now,'title':body.idea[:100],'idea':body.idea,'revision':1,
                            'mode':'evidence_plan','sources':sources,'evidence':evidence,'references':references,'sections':sections,
                            'warnings':['这是原文证据与待实施写作计划，不是已完成实验的论文。','书目身份与论点支持分别检查；来源ID/数字检查不能替代语义核验。']}
    if not sections[2]['paragraphs']:sections[2]['paragraphs']=[paragraph('plan','未检索到可引用正文，请补充可解析文献并核对原文。')]
    try:repo.create(payload)
    except ValueError:raise HTTPException(409,'创建时来源已删除')
    return payload


@router.post('/drafts')
def create_draft(body:DraftRequest):return build_draft(body)


class Paragraph(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(max_length=80)
    kind:Literal['quote','fact','analysis','plan']
    text:str=Field(min_length=1,max_length=12000)
    evidence_ids:list[str]=Field(default_factory=list,max_length=10)


class DraftSection(BaseModel):
    id:str=Field(max_length=80)
    title:str=Field(min_length=1,max_length=150)
    paragraphs:list[Paragraph]=Field(min_length=1,max_length=30)


class DraftPatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=200)
    sections:list[DraftSection]=Field(min_length=1,max_length=12)


def validate_sections(sections,evidence):
    by_id={item['id']:item for item in evidence};seen=set()
    for section in sections:
        for paragraph in section['paragraphs']:
            if paragraph['id'] in seen:raise ValueError('段落ID不可重复')
            seen.add(paragraph['id']);ids=paragraph['evidence_ids'];text=paragraph['text']
            if any(identity not in by_id for identity in ids):raise ValueError('引用ID不属于当前草稿证据')
            if paragraph['kind']=='quote':
                normalized=lambda value:re.sub(r'\s+',' ',value).strip()
                if len(ids)!=1 or normalized(text) not in normalized(by_id[ids[0]]['text']):raise ValueError('原文摘录必须逐字来自一个已选证据块')
                paragraph['review_status']='source_excerpt'
            elif paragraph['kind']=='fact':
                if not qa._validate_claims({'claims':[{'text':text,'evidence_ids':ids}]},evidence):raise ValueError('事实需有效来源，且不能增加来源中未报告的数字')
                paragraph['review_status']='source_and_numbers_checked_semantics_pending'
            elif paragraph['kind']=='analysis':paragraph['review_status']='user_analysis_unverified'
            else:paragraph['review_status']='planned_not_executed'
    return sections


@router.patch('/drafts/{identity}')
def update_draft(identity:str,body:DraftPatch):
    payload=copy.deepcopy(asset(identity,'draft'))
    try:
        payload.update(title=body.title,sections=validate_sections([section.model_dump() for section in body.sections],payload['evidence']))
        return repo.save_draft(payload,body.expected_revision)
    except ValueError as error:raise HTTPException(409 if str(error)=='revision_conflict' else 422,'草稿已有新版本，请重新打开' if str(error)=='revision_conflict' else str(error))


@router.get('/drafts/{identity}')
def get_draft(identity:str,revision:int|None=Query(default=None,ge=1)):return asset(identity,'draft',revision)


@router.get('/drafts/{identity}/revisions')
def draft_revisions(identity:str):
    asset(identity,'draft');return {'items':repo.revisions(identity)}


class ReferenceBinding(BaseModel):
    document_id:str
    record_key:str
    expected_revision:int=Field(ge=1)
    confirm_identity:Literal[True]


@router.post('/drafts/{identity}/reference')
def bind_reference(identity:str,body:ReferenceBinding):
    payload=copy.deepcopy(asset(identity,'draft'));document=store.get_document(body.document_id);record=literature_store.get_record(body.record_key)
    if not document or not record or body.document_id not in {source['document_id'] for source in payload['sources']}:raise HTTPException(422,'文献或注册记录不属于当前草稿')
    if graph.norm(record['title'])!=graph.norm(document['title']):raise HTTPException(422,'注册记录与本地标题不一致，不能强行绑定')
    author_words=[graph.norm(author.split()[-1]) for author in record.get('authors',[]) if author.split()]
    local_authors=graph.norm(' '.join(document.get('authors',[])))
    if not local_authors or not any(word and word in local_authors for word in author_words):raise HTTPException(422,'本地作者信息不足或不匹配，请先核对并编辑文献作者')
    payload['references']=[{'document_id':body.document_id,'metadata':record,'status':'user_confirmed_registry_identity','identity_basis':'user_confirmed_title_authors'} if item['document_id']==body.document_id else item for item in payload['references']]
    try:return repo.save_draft(payload,body.expected_revision)
    except ValueError:raise HTTPException(409,'版本或来源已变化')


def bib_escape(value):
    translation={'\\':r'\textbackslash{}','{':r'\{','}':r'\}','%':r'\%','&':r'\&','_':r'\_','#':r'\#','$':r'\$','^':r'\textasciicircum{}','~':r'\textasciitilde{}'}
    return ''.join(translation.get(character,character) for character in re.sub(r'\s+',' ',str(value))).strip()


def writing_exports(payload):
    references=[];sources=[];tokens={};bib=[]
    for item in payload['references']:
        metadata=item.get('metadata');document_id=item['document_id']
        if metadata:
            token=f'R{len(references)+1}';tokens[document_id]=token
            record_id=metadata['identifier'];key='ref_'+hashlib.sha256(metadata['key'].encode()).hexdigest()[:12]
            references.append(f'[{token}] '+', '.join(metadata.get('authors',[]))+f'. {metadata["title"]}. {metadata.get("year") or "年份未报告"}. {metadata["landing_url"]}')
            fields={'title':metadata['title'],'author':' and '.join(metadata.get('authors',[])),'year':metadata.get('year') or '', 'url':metadata['landing_url']}
            if metadata['provider']=='arxiv':fields.update(archivePrefix='arXiv',eprint=record_id)
            if metadata.get('doi') and literature.identify('doi:'+metadata['doi']):fields['doi']=metadata['doi']
            bib.append('@misc{'+key+',\n'+',\n'.join('  '+name+' = {'+bib_escape(value)+'}' for name,value in fields.items() if value)+'\n}')
        else:
            token=f'S{len(sources)+1}';tokens[document_id]=token
            source=next(source for source in payload['sources'] if source['document_id']==document_id)
            sources.append(f'[{token}] 本地来源文件：{source["title"]}；文献ID {document_id}；解析 {source["parse_run_id"]}。书目身份待核对，不冒充已验证出版物。')
    by_id={item['id']:item for item in payload['evidence']};lines=['# '+payload['title'],'',f'草稿版本 {payload["revision"]} · 研究计划与来源摘录，实验未被系统验证。','']
    labels={'quote':'原文摘录','fact':'待核验事实（仅来源/数字已检查）','analysis':'用户分析（未核验）','plan':'计划（未实施）'}
    for section in payload['sections']:
        lines += ['## '+section['title'],'']
        for paragraph in section['paragraphs']:
            citations=list(dict.fromkeys(tokens[by_id[key]['document_id']] for key in paragraph['evidence_ids']))
            lines += ['**'+labels[paragraph['kind']]+'** '+paragraph['text']+(' '+' '.join('['+token+']' for token in citations) if citations else ''),'']
            if paragraph['evidence_ids']:lines += ['来源位置：'+'；'.join(f'{by_id[key]["title"]} / PDF第{by_id[key]["page"]}页 / {by_id[key]["parse_run_id"]} / {by_id[key]["block_id"]}' for key in paragraph['evidence_ids']),'']
    lines += ['## References（注册记录对应）','']+references+['','## 本地来源（书目身份待核对）','']+sources+['']+payload['warnings']
    return '\n'.join(lines),'\n\n'.join(bib)+'\n'


@router.get('/drafts/{identity}/export')
def export_draft(identity:str,format:Literal['markdown','bibtex','bundle']='markdown',revision:int|None=Query(default=None,ge=1)):
    payload=asset(identity,'draft',revision);markdown,bibtex=writing_exports(payload)
    if format!='bundle':return PlainTextResponse(markdown if format=='markdown' else bibtex,media_type='text/markdown' if format=='markdown' else 'application/x-bibtex',headers={'Content-Disposition':f'attachment; filename="draft.{"md" if format=="markdown" else "bib"}"'})
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('draft.md',markdown);archive.writestr('references.bib',bibtex);archive.writestr('evidence.json',json.dumps(payload,ensure_ascii=False,indent=2))
    return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="writing-evidence.zip"'})
