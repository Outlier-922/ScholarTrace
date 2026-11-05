"""Confirmed, bounded import queues. Each advance runs one item; no hidden worker."""
import asyncio
import copy
import re
import uuid
import weakref
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from . import literature, literature_handoff as handoff, literature_store as repo, store

router=APIRouter(prefix='/api/literature/batches',tags=['literature-batches'])
INSTANCE=str(uuid.uuid4())
_locks=weakref.WeakValueDictionary()


class PreviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    search_id:str=Field(min_length=1,max_length=100)
    parent_task_id:str|None=Field(default=None,max_length=100)
    keys:list[Annotated[str,Field(min_length=1,max_length=250)]]=Field(min_length=1,max_length=5)


class VersionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0)


class Choice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(max_length=250)
    action:Literal['download','local']
    document_id:str|None=Field(default=None,max_length=100)


class ConfirmRequest(VersionRequest):
    confirm_identity:Literal[True]
    choices:list[Choice]=Field(min_length=1,max_length=5)


class RetryRequest(VersionRequest):
    key:str=Field(max_length=250)


def require(identity):
    value=repo.batch_get(identity)
    if not value:raise HTTPException(404,'批量入库记录不存在')
    return value


def save(value):
    old=value['revision']; value['revision']+=1;value['updated_at']=store.now()
    if not repo.batch_replace(value,old):raise HTTPException(409,'队列已更新，请刷新后继续')


def context(value,key):
    return handoff.Context(key=key,search_id=value['search_id'],parent_task_id=value['parent_task_id'])


def validate_metadata(value,item):
    if handoff.metadata(context(value,item['key']))!=item['metadata']:
        raise HTTPException(409,'原检索快照发生变化，请重新预览核对')


def view(value):
    value=copy.deepcopy(value)
    for item in value['items']:
        if item['status']=='running' and item.get('owner')!=INSTANCE:
            item.update(status='interrupted',error='服务在执行中重启；请明确重试此项。')
        if item.get('handoff_id') and item['status']!='skipped':
            receipt=repo.get_handoff(item['handoff_id'])
            resolved=handoff.resolve(receipt) if receipt else None
            item['handoff']=resolved
            if resolved and resolved['can_plan']:item.update(status='ready',error=None)
            elif resolved and resolved['status']=='pending':item.update(status='waiting_parse',error=None)
            else:item.update(status='source_unavailable',error=resolved['notice'] if resolved else '全文交接记录不可用')
    states={item['status'] for item in value['items']}
    value['status']='draft' if not value['confirmed'] else 'running' if 'running' in states else 'blocked' if 'interrupted' in states else 'queued' if 'queued' in states else 'blocked' if states & {'failed','source_unavailable'} else 'waiting_parse' if 'waiting_parse' in states else 'completed_with_gaps' if 'skipped' in states else 'completed'
    value['counts']={state:sum(i['status']==state for i in value['items']) for state in sorted(states)}
    value['download_item_attempts']=sum(i['attempts'] for i in value['items'] if (i.get('choice') or {}).get('action')=='download')
    return value


@router.post('')
def preview(body:PreviewRequest):
    if len(set(body.keys))!=len(body.keys):raise HTTPException(422,'不能重复选择同一条书目')
    value={'id':str(uuid.uuid4()),'revision':0,'search_id':body.search_id,'parent_task_id':body.parent_task_id,
        'created_at':store.now(),'updated_at':store.now(),'confirmed':False,'items':[],
        'limits':{'papers':5,'attempts_per_item':2},'semantic_verified':False,
        'warnings':['只处理本次明确选择的书目，不递归扩展参考文献。',
            '关闭页面后不再自动启动下一篇；当前请求可能继续，重开后读取保存进度。',
            '元数据和全文可获取不代表引用价值已确认。新研究任务仍使用独立计划和预算。']}
    for key in body.keys:
        item=handoff.metadata(context(value,key))
        if item['provider']!='arxiv' or not item.get('pdf_url') or not re.search(r'v[1-9]\d*$',item['identifier']):
            raise HTTPException(422,'批量入口暂限已返回确切版本的arXiv记录；其他来源请使用单篇核对路径')
        candidates=[{k:doc[k] for k in ('id','title','filename','sha256','parse_run_id','status')} for doc in handoff.candidates(item)]
        value['items'].append({'key':key,'metadata':item,'candidates':candidates,'status':'unconfirmed','attempts':0,'history':[]})
    repo.batch_insert(value)
    return view(value)


@router.get('')
def history():return {'items':[{'id':v['id'],'created_at':v['created_at'],'search_id':v['search_id'],'confirmed':v['confirmed'],'titles':[i['metadata']['title'] for i in v['items']]} for v in repo.batch_list()]}


@router.get('/{identity}')
def detail(identity:str):return view(require(identity))


@router.get('/{identity}/export')
def export(identity:str):return JSONResponse(view(require(identity)),headers={'Content-Disposition':'attachment; filename="literature-batch.json"'})


@router.post('/{identity}/confirm')
async def confirm(identity:str,body:ConfirmRequest):
    lock=_locks.setdefault(identity,asyncio.Lock())
    async with lock:
        value=require(identity);choices={c.key:c.model_dump() for c in body.choices}
        if len(choices)!=len(body.choices) or set(choices)!={i['key'] for i in value['items']}:
            raise HTTPException(422,'请逐项确认预览中的全部书目')
        if value['confirmed']:
            if all(i['choice']==choices[i['key']] for i in value['items']):return view(value)
            raise HTTPException(409,'已确认的队列不可更换来源，请新建预览')
        if body.revision!=value['revision']:raise HTTPException(409,'队列已更新，请刷新')
        for item in value['items']:
            validate_metadata(value,item);choice=choices[item['key']]
            if choice['action']=='local':
                doc=next((c for c in item['candidates'] if c['id']==choice['document_id']),None)
                if not doc:raise HTTPException(422,'本地全文不属于此项已预览候选')
                # Validate now, bind only when this queued item executes.
                current=store.get_document(doc['id'])
                if not current or any(current[k]!=doc[k] for k in ('sha256','parse_run_id')):
                    raise HTTPException(409,'本地候选发生变化，请重新预览')
                if not handoff.resolve({'source':{k:doc[k] for k in ('id','sha256','parse_run_id')}})['can_plan']:
                    raise HTTPException(409,'本地候选解析未就绪或文件不可用')
                item['local_snapshot']=doc
            elif choice['document_id'] is not None:raise HTTPException(422,'下载操作不能附带本地文件ID')
            item.update(choice=choice,status='queued')
        value['confirmed']=True;save(value)
        return view(value)


@router.post('/{identity}/advance')
async def advance(identity:str,body:VersionRequest,request:Request):
    lock=_locks.setdefault(identity,asyncio.Lock())
    async with lock:
        value=require(identity)
        if body.revision!=value['revision']:raise HTTPException(409,'队列已更新，请读取进度；没有重复执行')
        if not value['confirmed']:raise HTTPException(409,'请先确认书目身份和每项操作')
        if any(i['status']=='running' for i in value['items']):raise HTTPException(409,'有正在执行或中断的项，请读取进度并处理')
        item=next((i for i in value['items'] if i['status']=='queued'),None)
        if not item:return view(value)
        item.update(status='running',owner=INSTANCE,attempts=item['attempts']+1,error=None)
        attempt={'started_at':store.now(),'attempt':item['attempts']};item['history'].append(attempt);save(value)
        try:
            validate_metadata(value,item)
            ctx=context(value,item['key'])
            if item['choice']['action']=='local':
                doc=item['local_snapshot']
                result=handoff.confirm_local(handoff.LocalRequest(**ctx.model_dump(),document_id=doc['id'],parse_run_id=doc['parse_run_id'],sha256=doc['sha256'],confirm_identity=True))
            else:
                # A receipt saved before a process interruption prevents another download.
                recovered=[r for r in repo.matching_handoffs(item['key'],value['search_id'],value['parent_task_id'])
                    if r['origin']=='downloaded_pdf' and r['metadata']==item['metadata'] and r['created_at']>=item['history'][0]['started_at']]
                if len(recovered)>1:raise HTTPException(409,'存在多条恢复候选，请从后续研究逐项核对')
                if recovered:
                    result=handoff.resolve(recovered[0]);attempt['recovered_receipt']=True
                else:
                    imported=await literature.import_paper(literature.ImportRequest(**ctx.model_dump(),confirm_identity=True),request)
                    result=imported['handoff']
            item.update(handoff_id=result['id'],status='waiting_parse')
            attempt['outcome']='handoff_saved'
        except HTTPException as error:
            item.update(status='failed',error=str(error.detail) if isinstance(error.detail,str) else '此项未完成，请核对来源后再试')
            attempt.update(outcome='failed',http_status=error.status_code)
        except Exception:
            item.update(status='failed',error='此项执行异常，进度已保留；请核对后明确重试。')
            attempt['outcome']='failed'
        except asyncio.CancelledError:
            item.update(status='failed',error='请求已中断，可能已获取文件；请核对后明确重试。')
            attempt.update(outcome='interrupted',finished_at=store.now());save(value)
            raise
        attempt['finished_at']=store.now();save(value)
        return view(value)


@router.post('/{identity}/retry')
async def retry(identity:str,body:RetryRequest):
    lock=_locks.setdefault(identity,asyncio.Lock())
    async with lock:
        value=require(identity)
        if value['revision']!=body.revision:raise HTTPException(409,'队列已更新，请刷新')
        item=next((i for i in view(value)['items'] if i['key']==body.key),None)
        if not item or item['status'] not in {'failed','interrupted'} or item['attempts']>=2:
            raise HTTPException(409,'此项不能重试；每项最多执行两次，解析/来源问题需在文献库处理')
        stored=next(i for i in value['items'] if i['key']==body.key)
        stored.update(status='queued',error=None);save(value)
        return view(value)


@router.post('/{identity}/skip')
async def skip(identity:str,body:RetryRequest):
    lock=_locks.setdefault(identity,asyncio.Lock())
    async with lock:
        value=require(identity)
        if value['revision']!=body.revision:raise HTTPException(409,'队列已更新，请刷新')
        item=next((i for i in view(value)['items'] if i['key']==body.key),None)
        if not item or item['status'] not in {'failed','interrupted','source_unavailable'}:
            raise HTTPException(409,'只能跳过失败、中断或来源不可用的项目')
        stored=next(i for i in value['items'] if i['key']==body.key)
        stored.update(status='skipped',skipped_at=store.now(),skipped_from=item['status'],error=item.get('error'))
        save(value)
        return view(value)
