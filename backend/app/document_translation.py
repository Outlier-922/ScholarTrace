"""Full-document manifests with explicit, metered batches and page-aligned results."""
import asyncio
import hashlib
import html
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, PlainTextResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from . import model_client, research_store, source_scope, store, translation
from . import document_translation_store as repo

router=APIRouter(prefix='/api/document-translations',tags=['bounded-document-translation'])
_workers={}
_slots=asyncio.Semaphore(1)
_closing=False
DONE={'translated','cached','preserved'}
ACTIVE={'queued','running','pause_requested','cancel_requested'}


class PlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:str
    parse_run_id:str
    scope:Literal['document','pages','blocks']='document'
    page_start:int|None=Field(default=None,ge=1)
    page_end:int|None=Field(default=None,ge=1)
    block_ids:list[str]=Field(default_factory=list,max_length=300)
    target_language:Literal['zh','en']='zh'
    glossary:dict[str,str]=Field(default_factory=dict)


class BatchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['start','resume','retry','pause','cancel']
    expected_revision:int=Field(ge=1)
    allow_model:bool=False
    max_model_calls:int=Field(default=3,ge=0,le=20)
    max_output_tokens:int=Field(default=10500,ge=0,le=70000)
    max_units:int=Field(default=50,ge=1,le=1000)
    max_active_seconds:int=Field(default=180,ge=5,le=900)


class EditRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    block_id:str
    source_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    text:str=Field(min_length=1,max_length=96000)
    reason:str=Field(min_length=1,max_length=2000)
    origin:Literal['user_edit','assistant_source_review']='user_edit'


def fingerprint(block):
    return hashlib.sha256(json.dumps({key:block[key] for key in ('id','text','page','bbox','type')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def source(job):
    frozen=job['source'];document=store.get_internal_document(frozen['document_id'])
    if not document or document['sha256']!=frozen['sha256']:raise HTTPException(409,'翻译原始文件不可用或已变化')
    try:
        with Path(document['file_path']).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:raise HTTPException(409,'翻译原始文件不可用')
    if digest!=frozen['sha256']:raise HTTPException(409,'PDF哈希变化，停止使用旧翻译计划')
    run=store.get_parse_run(frozen['document_id'],frozen['parse_run_id'])
    if not run or run['status'] not in {'ready','partial'}:raise HTTPException(409,'已绑定解析版本尚不可用')
    return frozen


def checked_block(job,unit):
    structure=store.get_structure(job['document_id'],page=unit['page'],parse_run_id=job['source']['parse_run_id'])
    block=next((block for block in (structure or {}).get('blocks',[]) if block['id']==unit['block_id']),None)
    if not block or fingerprint(block)!=unit['source_sha256']:raise HTTPException(409,'原文段落或位置已变化，停止翻译')
    return block


def preserve_reason(block,reference_ids):
    if block['id'] in reference_ids or any(word in str(block.get('section_path','')).casefold() for word in ('references','bibliography','参考文献')):return '参考文献原样保留'
    if block['type'] in {'figure','formula','formula_candidate','code','table','table_candidate'}:return '图像、公式、代码或表格原样保留'
    if not block['text'].strip():return '无文本内容，参看原PDF'
    protected,_=translation.protect_text(block['text'],{})
    import re
    if not re.sub(r'\[STP_[^\]]+\]|[\W\d_]','',protected):return '仅编号或受保护片段，原样保留'
    if re.match(r'^\s*arXiv:',block['text'],re.I):return '书目标识与版本原样保留'
    return None


def build_plan(body,artifact_id=None,persist=True):
    if artifact_id and (existing:=repo.get(artifact_id)):return existing
    translation._validate_request(translation.TranslationRequest(document_id=body.document_id,target_language=body.target_language,glossary=body.glossary))
    document=store.get_document(body.document_id)
    if not document:raise HTTPException(404,'文献不存在')
    frozen={'document_id':document['id'],'parse_run_id':body.parse_run_id,'sha256':document['sha256'],'title':document['title'],'filename':document['filename'],'page_count':document['page_count']}
    source({'source':frozen})
    structure=store.get_structure(body.document_id,parse_run_id=body.parse_run_id)
    if not structure or not structure['blocks']:raise HTTPException(409,'没有可用原文内容')
    if body.scope=='pages':
        if not body.page_start or not body.page_end or not 1<=body.page_start<=body.page_end<=document['page_count']:raise HTTPException(422,'请明确有效页码范围')
    elif body.page_start is not None or body.page_end is not None:raise HTTPException(422,'仅页范围模式接受起止页')
    if (body.scope=='blocks')!=bool(body.block_ids):raise HTTPException(422,'段落模式需选择明确原文块；其他模式不接受段落ID')
    blocks=structure['blocks']
    if body.scope=='pages':blocks=[block for block in blocks if body.page_start<=block['page']<=body.page_end]
    if body.scope=='blocks':
        if len(set(body.block_ids))!=len(body.block_ids) or not set(body.block_ids)<={block['id'] for block in blocks}:raise HTTPException(422,'原文块无效或重复')
        blocks=[block for block in blocks if block['id'] in body.block_ids]
    if not blocks or len(blocks)>20000 or sum(len(block['text']) for block in blocks)>5_000_000:raise HTTPException(422,'范围需在1—20000块、500万字符以内；超出时请按章节或页码规划')
    references=source_scope.reference_block_ids(structure);units=[]
    with store.connection() as conn:cached={row[0] for row in conn.execute('SELECT cache_key FROM translations WHERE document_id=? AND parse_run_id=? AND language=?',(body.document_id,body.parse_run_id,body.target_language))}
    cache_hits=0
    for block in blocks:
        reason=preserve_reason(block,references)
        key=translation.cache_key(body.document_id,body.parse_run_id,block,body.target_language,body.glossary)
        status='preserved' if reason else 'blocked' if len(block['text'])>12000 else 'pending'
        if status=='pending' and key in cached:cache_hits+=1
        units.append({'block_id':block['id'],'page':block['page'],'block_type':block['type'],'source_sha256':fingerprint(block),
            'character_count':len(block['text']),'status':status,'reason':reason or ('段落超过12000字符，需拆分后重新规划' if status=='blocked' else ''),
            'cache_key':key,'translation':None,'attempts':0})
    counts={status:sum(unit['status']==status for unit in units) for status in ('pending','preserved','blocked')}
    requested_pages=set(range(1,document['page_count']+1)) if body.scope=='document' else set(range(body.page_start,body.page_end+1)) if body.scope=='pages' else {unit['page'] for unit in units}
    covered_pages={unit['page'] for unit in units}
    now=store.now();job={'id':artifact_id or str(uuid.uuid4()),'document_id':body.document_id,'source':frozen,'spec':body.model_dump(),
        'model_identity':model_client.identity(),'prompt_version':translation.PROMPT_VERSION,'manifest_version':'document-translation-v1',
        'manifest_digest':hashlib.sha256(json.dumps([(unit['block_id'],unit['source_sha256']) for unit in units]).encode()).hexdigest(),
        'status':'planned','control':None,'revision':1,'created_at':now,'updated_at':now,'total':len(units),'counts':counts,
        'character_count':sum(unit['character_count'] for unit in units),'page_count':len(requested_pages),'block_page_count':len(covered_pages),
        'pages_without_blocks':sorted(requested_pages-covered_pages),
        'estimate':{'cache_hits':cache_hits,'uncached_requests':counts['pending']-cache_hits,'output_token_reserve':3500*(counts['pending']-cache_hits),'notice':'请求数是当前缓存条件下的上界，不是价格或译文质量保证。'},
        'batch':None,'history':[],'totals':{'batches':0,'calls':0,'reserved_output_tokens':0,'input_tokens':0,'output_tokens':0,'unreported_usage':0},
        'notice':'计划已保存，尚未启动；确认范围与本批预算后执行。','semantic_verified':False}
    if persist:repo.create(job,units)
    return job


def require(identity):
    value=repo.get(identity)
    if not value:raise HTTPException(404,'全文翻译计划或原文不存在')
    return value


def detail(identity,page=None):
    job=require(identity);source(job);units=repo.items(identity,page)
    structure=store.get_structure(job['document_id'],page=page,parse_run_id=job['source']['parse_run_id'])
    blocks={block['id']:block for block in (structure or {}).get('blocks',[])}
    for unit in units:
        block=blocks.get(unit['block_id'])
        if not block or fingerprint(block)!=unit['source_sha256']:raise HTTPException(409,'原文段落或位置已变化')
        unit['source']=block
    return {**job,'items':units}


@router.post('')
def plan(body:PlanRequest):return build_plan(body)


@router.get('')
def listing(document_id:str,parse_run_id:str|None=None):return {'items':repo.list_jobs(document_id,parse_run_id)}


@router.get('/{identity}')
def get(identity:str,page:int|None=Query(default=None,ge=1)):
    job=require(identity)
    if page is None:return job  # Whole manifests are exported explicitly, not returned on every poll.
    return detail(identity,page)


@router.post('/{identity}/edits')
def edit(identity:str,body:EditRequest):
    job=require(identity);source(job)
    if job['status'] in ACTIVE:raise HTTPException(409,'请先暂停本批，再校订译文')
    unit=next((unit for unit in repo.items(identity) if unit['block_id']==body.block_id),None)
    if not unit or unit['translation'] is None or unit['status'] not in {'translated','cached'}:raise HTTPException(422,'只能校订已生成或缓存的译文，原文保留块不可冒充译文')
    block=checked_block(job,unit)
    if body.source_sha256!=unit['source_sha256']:raise HTTPException(409,'校订的原文版本已变化')
    if not body.text.strip() or len(body.text)>max(2000,len(block['text'])*8) or translation._numbers(body.text)!=translation._numbers(block['text']):raise HTTPException(422,'校订需保留原文数字，不得增加数据')
    _,replacements=translation.protect_text(block['text'],job['spec']['glossary'])
    from collections import Counter
    if any(body.text.count(value)<count for value,count in Counter(replacements.values()).items()):raise HTTPException(422,'校订需保留原文公式、标识、单位和受保护术语')
    edits=unit.get('edits',[])
    record={'version':len(edits)+1,'text':body.text,'reason':body.reason,'origin':body.origin,'created_at':store.now(),'source_sha256':body.source_sha256,'semantic_verified':False}
    unit.update(edits=edits+[record],reviewed_translation=body.text)
    def update(value):value['edit_count']=value.get('edit_count',0)+1
    try:return repo.mutate(identity,update,body.expected_revision,unit=unit)
    except ValueError as error:raise HTTPException(409,'计划已有新版本，请刷新后再校订') from error


@router.post('/{identity}/control')
async def control(identity:str,body:BatchRequest):
    job=require(identity)
    if body.action in {'start','resume','retry'}:
        source(job)
        if body.allow_model and (not model_client.configuration()['ready'] or job['model_identity']!=model_client.identity() or job['prompt_version']!=translation.PROMPT_VERSION):raise HTTPException(409,'模型或翻译版本不可用/已变化，请恢复原配置或新建计划')
    def change(value):
        if body.action in {'pause','cancel'}:
            if value['status'] in {'completed','completed_with_gaps','cancelled'}:raise ValueError('invalid_state')
            value['control']=body.action;value['status']=body.action+'_requested' if value['status'] in ACTIVE else 'paused' if body.action=='pause' else 'cancelled'
            return
        if value['status'] not in {'planned','paused','budget_exhausted','failed'} or value['status']=='failed' and body.action!='retry':raise ValueError('invalid_state')
        if value['batch']:value['history']=(value['history']+[value['batch']])[-100:]
        value['totals']['batches']+=1
        value.update(status='queued',control=None,notice='已确认本批预算；不会自动开始下一批。',
            batch={'number':value['totals']['batches'],'allow_model':body.allow_model,'limits':{key:getattr(body,key) for key in ('max_model_calls','max_output_tokens','max_units','max_active_seconds')},
                   'usage':model_client.CallBudget(0,0).snapshot(),'processed':0,'active_seconds':0.,'started_at':store.now()})
    try:value=repo.mutate(identity,change,body.expected_revision)
    except ValueError as error:raise HTTPException(409,'状态或版本已变化，请刷新；失败段落需明确选择重试') from error
    if value['status']=='queued':schedule(identity)
    return value


def schedule(identity):
    if _closing:return
    if identity in _workers and not _workers[identity].done():return
    async def guarded():
        async with _slots:await run(identity)
    worker=asyncio.create_task(guarded());_workers[identity]=worker
    def finished(done):
        if _workers.get(identity) is done:_workers.pop(identity,None)
        if not done.cancelled():done.exception()
        if not _closing and (job:=repo.get(identity)) and job['status']=='queued':schedule(identity)
    worker.add_done_callback(finished)


async def run(identity):
    try:
        job=repo.get(identity)
        if not job or job['status']!='queued':return
        source(job);repo.mutate(identity,lambda value:value.update(status='running'))
        while (job:=repo.get(identity)):
            if job['control'] in {'pause','cancel'}:
                repo.mutate(identity,lambda value:value.update(status='paused' if value['control']=='pause' else 'cancelled'));return
            if job['status']!='running':return
            pending=repo.items(identity,pending=True,limit=1)
            if not pending:
                source(job);repo.mutate(identity,lambda value:value.update(status='completed_with_gaps' if value['counts'].get('blocked',0) or value['pages_without_blocks'] else 'completed',notice='所选范围已处理；原样保留、未支持块及无解析内容的页面单独列出，学术含义仍需核验。'));return
            batch=job['batch'];limits=batch['limits']
            if batch['processed']>=limits['max_units'] or batch['active_seconds']>=limits['max_active_seconds']:
                repo.mutate(identity,lambda value:value.update(status='budget_exhausted',notice='本批达到段落/活动时间上限；完成段落保留，需明确确认下一批。'));return
            unit=pending[0];block=checked_block(job,unit)
            cached=research_store.get_translation(unit['cache_key'])
            if not cached and not batch['allow_model']:
                repo.mutate(identity,lambda value:value.update(status='paused',notice='本批仅使用缓存；此段无译文，尚未调用模型。可确认生成预算后继续。'));return
            if not cached and (job['model_identity']!=model_client.identity() or job['prompt_version']!=translation.PROMPT_VERSION):raise ValueError('model_changed')
            budget=model_client.CallBudget(limits['max_model_calls'],limits['max_output_tokens'],**batch['usage'])
            def observe(values):
                def update(value):
                    old=value['batch']['usage']
                    for key in value['totals']:
                        if key!='batches':value['totals'][key]+=values[key]-old[key]
                    value['batch']['usage']=values
                repo.mutate(identity,update)
            budget.observer=observe;started=time.monotonic();unit.update(status='running',attempts=unit['attempts']+1)
            repo.mutate(identity,lambda value:None,unit=unit)
            try:
                if cached:text=cached['translated_text'];status='cached'
                else:
                    with model_client.metered(budget):_,text,status=await translation.translate_block(job['document_id'],job['source']['parse_run_id'],block,job['spec']['target_language'],job['spec']['glossary'])
                checked_block(job,unit)
            except asyncio.CancelledError:raise
            except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError) as error:
                exhausted=isinstance(error,ValueError) and str(error)=='model_budget_exhausted'
                unit.update(status='pending' if exhausted else 'failed',error_code='model_budget_exhausted' if exhausted else type(error).__name__)
                def fail(value):
                    value['batch']['active_seconds']+=round(time.monotonic()-started,3)
                    value.update(status='budget_exhausted' if exhausted else 'failed',notice='本批模型预算已用尽，未发出该请求。' if exhausted else '本段调用或内容保护检查失败；完成段落保留，不自动重发。')
                repo.mutate(identity,fail,unit=unit);return
            unit.update(status=status,translation=text,error_code=None)
            def complete(value):
                value['batch']['processed']+=1;value['batch']['active_seconds']+=round(time.monotonic()-started,3)
            repo.mutate(identity,complete,unit=unit)
    except asyncio.CancelledError:
        if repo.get(identity):
            for unit in repo.items(identity,pending=True):
                if unit['status']=='running':unit.update(status='pending',interrupted=True);repo.mutate(identity,lambda value:None,unit=unit)
            repo.mutate(identity,lambda value:value.update(status='paused',notice='执行中断，完成段落保留；需明确继续，中断请求可能已计费。'))
        raise
    except Exception:
        if repo.get(identity):repo.mutate(identity,lambda value:value.update(status='failed',notice='原文、模型或执行范围变化，已停止；不会自动换版本。'))


def start():
    global _closing
    _closing=False;repo.recover()


async def shutdown():
    global _closing
    _closing=True;workers=list(_workers.values())
    for worker in workers:worker.cancel()
    if workers:await asyncio.gather(*workers,return_exceptions=True)
    _workers.clear()


def export_text(job,units):
    lines=['# 原文与译文对照',f"状态：{job['status']}；{job['source']['filename']}；ParseRun {job['source']['parse_run_id']}；SHA-256 {job['source']['sha256']}",
           '仅已生成/缓存段落有译文；原样保留不等于翻译，未处理/失败/超长块不算完成。语义未核验。']
    sections=[]
    for unit in units:
        source_text=unit['source']['text'];translated=unit.get('reviewed_translation') or (unit['translation'] if unit['translation'] is not None else '【原样保留：'+unit['reason']+'】' if unit['status']=='preserved' else '【尚未翻译：'+(unit.get('reason') or unit['status'])+'】')
        label=f"PDF第{unit['page']}页 / {unit['block_id']} / {unit['status']}"+(' / 校订候选（未独立核验）' if unit.get('edits') else '')
        lines+=['','## '+label,'',source_text,'',translated]
        sections.append('<section><header>'+html.escape(label)+'</header><div class="pair"><pre>'+html.escape(source_text)+'</pre><pre>'+html.escape(translated)+'</pre></div></section>')
    page='<!doctype html><html lang="zh"><meta charset="utf-8"><title>原文与译文对照</title><style>body{font:16px/1.7 system-ui;max-width:1400px;margin:30px auto;padding:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}section{border-bottom:1px solid #ddd;padding:20px}header{font-size:12px}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style><h1>原文与译文对照</h1><p>'+html.escape(lines[1])+'</p><p>'+html.escape(lines[2])+'</p>'+''.join(sections)+'</html>'
    return '\n'.join(lines),page


@router.get('/{identity}/export')
def export(identity:str,format:Literal['bundle','markdown','html']='bundle'):
    payload=detail(identity);units=payload.pop('items');md,page=export_text(payload,units)
    if format=='markdown':return PlainTextResponse(md,headers={'Content-Disposition':'attachment; filename="parallel-translation.md"'})
    if format=='html':return HTMLResponse(page,headers={'Content-Disposition':'attachment; filename="parallel-translation.html"','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'"})
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('translation.json',json.dumps({**payload,'items':units},ensure_ascii=False,indent=2))
        archive.writestr('translation.md',md);archive.writestr('translation.html',page)
    return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="parallel-translation.zip"'})
