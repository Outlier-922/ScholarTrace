"""Public scholarly metadata discovery. No model-generated paper identities or URLs."""
from __future__ import annotations
import asyncio
import hashlib
import html
import json
import os
import re
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from . import literature_store as repo, store

router=APIRouter(prefix='/api',tags=['literature'])
ARXIV=re.compile(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v[1-9]\d*)?',re.I)
DOI=re.compile(r'10\.\d{4,9}/\S+',re.I)
NS={'a':'http://www.w3.org/2005/Atom','x':'http://arxiv.org/schemas/atom','o':'http://a9.com/-/spec/opensearch/1.1/'}
_gates={'arxiv':asyncio.Lock(),'crossref':asyncio.Lock()}
_last={'arxiv':0.,'crossref':0.}
_blocked_until={'arxiv':0.,'crossref':0.}
_checking=set()
_scheduler=None


class SearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=2,max_length=500)
    provider:Literal['arxiv','crossref','both']='both'
    mode:Literal['topic','title','reference']='title'
    author:str=Field(default='',max_length=120)
    year:int|None=Field(default=None,ge=1600,le=2100)
    limit:int=Field(default=10,ge=1,le=20)
    include_disliked:bool=False


def identify(value):
    value=unquote(value.strip())
    value=re.sub(r'^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/','',value,flags=re.I)
    value=re.sub(r'^arxiv:\s*','',value,flags=re.I).removesuffix('.pdf')
    if ARXIV.fullmatch(value): return 'arxiv',value.lower()
    value=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',value,flags=re.I)
    if DOI.fullmatch(value) and not any(char in value for char in '\\<>"'): return 'crossref',value.lower()
    return None


def plain(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',str(value or '')))).strip()


def words(value):
    return set(re.findall(r'[a-z][a-z0-9-]{2,}|[\u4e00-\u9fff]{2,}',value.casefold()))-{'the','and','for','with','from','paper','study','using','based','all','need','you'}


def safe_url(value):
    try:
        parsed=urlsplit(value or '')
        return value if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment else None
    except ValueError: return None


async def _fetch(provider,params=None,doi=None):
    """Fixed official endpoints, serialized calls, bounded bodies, no redirects."""
    endpoint='https://export.arxiv.org/api/query' if provider=='arxiv' else 'https://api.crossref.org/works'+('/'+quote(doi,safe='') if doi else '')
    headers={'User-Agent':'ScholarTrace/0.4 (local academic research assistant)'}
    contact=os.getenv('SCHOLARTRACE_CONTACT_EMAIL','').strip()
    params=dict(params or {})
    if provider=='crossref' and contact: params['mailto']=contact
    async with _gates[provider]:
        if _blocked_until[provider]>time.monotonic():
            raise httpx.HTTPStatusError('provider cooldown',request=httpx.Request('GET',endpoint),response=httpx.Response(429))
        delay=(3.05 if provider=='arxiv' else .25)-(time.monotonic()-_last[provider])
        if delay>0: await asyncio.sleep(delay)
        _last[provider]=time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=10),follow_redirects=False) as client:
            async with client.stream('GET',endpoint,params=params,headers=headers) as response:
                if response.status_code==429:
                    retry=response.headers.get('retry-after','60')
                    _blocked_until[provider]=time.monotonic()+min(3600,max(3,int(retry) if retry.isdigit() else 60))
                response.raise_for_status()
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>4_000_000: raise ValueError('metadata_body_too_large')
                return bytes(body)


def parse_arxiv(body):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper(): raise ValueError('invalid_xml')
    root=ET.fromstring(body)
    if root.tag!='{'+NS['a']+'}feed': raise ValueError('invalid_feed')
    records=[]
    for entry in root.findall('a:entry',NS):
        raw=entry.findtext('a:id','',NS)
        parsed=identify(raw)
        if not parsed or parsed[0]!='arxiv': continue
        identity=parsed[1]
        if not re.search(r'v\d+$',identity): continue  # Freeze a specific revision for imports.
        title=plain(entry.findtext('a:title','',NS))
        if not title or title.casefold()=='error': continue
        records.append({'key':'arxiv:'+identity,'root_key':'arxiv:'+re.sub(r'v\d+$','',identity),'provider':'arxiv','identifier':identity,
            'title':title,'authors':[plain(author.findtext('a:name','',NS)) for author in entry.findall('a:author',NS)],
            'published':entry.findtext('a:published','',NS),'updated':entry.findtext('a:updated','',NS),
            'year':int(entry.findtext('a:published','0000',NS)[:4]),'abstract':plain(entry.findtext('a:summary','',NS))[:15000],
            'doi':plain(entry.findtext('x:doi','',NS)) or None,'venue':plain(entry.findtext('x:journal_ref','',NS)) or 'arXiv preprint',
            'landing_url':'https://arxiv.org/abs/'+identity,'pdf_url':'https://arxiv.org/pdf/'+identity,
            'fulltext_status':'arxiv_pdf_available','identity_status':'registry_record','retrieved_at':store.now(),
            'source_url':'https://export.arxiv.org/api/query?id_list='+quote(identity,safe='')})
    total=int(root.findtext('o:totalResults',str(len(records)),NS))
    return {'items':records,'total':total}


def parse_crossref(body,exact=False):
    result=json.loads(body)
    if result.get('status')!='ok' or not isinstance(result.get('message'),dict): raise ValueError('invalid_crossref_payload')
    message=result['message']; entries=[message] if exact else message.get('items',[])
    items=[]
    for entry in entries:
        doi=plain(entry.get('DOI')).lower()
        title=plain((entry.get('title') or [''])[0])
        if not DOI.fullmatch(doi) or not title: continue
        date=entry.get('published',entry.get('issued',{})).get('date-parts',[[]])[0]
        items.append({'key':'crossref:'+doi,'root_key':'crossref:'+doi,'provider':'crossref','identifier':doi,'doi':doi,'title':title,
            'authors':[plain(' '.join([author.get('given',''),author.get('family','')])) for author in entry.get('author',[])][:200],
            'year':date[0] if date else None,'published':'-'.join(str(part) for part in date),'updated':entry.get('indexed',{}).get('date-time'),
            'abstract':plain(entry.get('abstract'))[:15000],'venue':plain((entry.get('container-title') or [''])[0]),
            'landing_url':'https://doi.org/'+quote(doi,safe='/'),'pdf_url':None,'fulltext_status':'metadata_only',
            'identity_status':'registry_record','retrieved_at':store.now(),'source_url':'https://api.crossref.org/works/'+quote(doi,safe='')})
    return {'items':items,'total':message.get('total-results',len(items))}


async def search_provider(provider,query,mode,limit,*,fresh=False,latest=False):
    exact=identify(query)
    cache_key=hashlib.sha256(json.dumps(['query-v2',provider,query,mode,limit,latest],ensure_ascii=False).encode()).hexdigest()
    if not fresh and (cached:=repo.cache_get(cache_key)):
        return {**cached,'cache':'hit'}
    if provider=='arxiv':
        if exact and exact[0]=='arxiv': params={'id_list':exact[1]}
        else:
            terms=re.findall(r'[\w-]+',query)[:18]
            params={'search_query':'ti:"'+' '.join(terms)+'"' if mode=='title' else ' AND '.join(f'all:"{term}"' for term in terms)}
        params.update(max_results=limit,sortBy='lastUpdatedDate' if latest else 'relevance',sortOrder='descending')
        result=parse_arxiv(await _fetch('arxiv',params))
    else:
        if exact and exact[0]=='crossref': result=parse_crossref(await _fetch('crossref',doi=exact[1]),True)
        else: result=parse_crossref(await _fetch('crossref',{'query.bibliographic' if mode=='reference' else 'query.title' if mode=='title' else 'query':query,'rows':limit}))
    if exact:
        result['items']=[item for item in result['items'] if item['identifier']==exact[1] or (exact[0]=='arxiv' and re.sub(r'v\d+$','',item['identifier'])==exact[1])]
    result.update(provider=provider,cache='miss',fetched_at=store.now())
    repo.save_records(result['items']); repo.cache_put(cache_key,result)
    return result


def decorate(items,request):
    preferences=repo.feedback()
    liked=[repo.get_record(key) for key,value in preferences.items() if value==1]
    interest=set().union(*(words(item['title']) for item in liked if item)) if liked else set()
    output=[]
    for index,raw in enumerate(items):
        item=dict(raw); topic=words(request.query); terms=words(item['title']+' '+item.get('abstract',''))
        shared=sorted(topic & terms); personal=sorted(interest & terms)
        value=preferences.get(item['key'],0)
        if value==-1 and not request.include_disliked: continue
        checks={'identifier_exact': bool(identify(request.query)), 'author_match':request.author.casefold() in ' '.join(item['authors']).casefold() if request.author else None,
                'year_match':item['year']==request.year if request.year else None}
        item.update(feedback=value,identity_checks=checks,reasons=([f"查询关键词匹配：{', '.join(shared[:6])}"] if shared else ['由书目服务返回的候选，需核对标题与作者'])+
                    ([f"与你点赞文献共享词：{', '.join(personal[:5])}"] if personal else [])+
                    ['arXiv 可获取原版 PDF' if item['pdf_url'] else '当前只有书目元数据，不能用于原文问答'],
                    _rank=len(shared)/max(1,len(topic))+.15*min(len(personal),3)/3+.05*value-index*.0001)
        output.append(item)
    output.sort(key=lambda item:item['_rank'],reverse=True)
    for item in output: item.pop('_rank')
    return output


@router.post('/literature/search')
async def search(request:SearchRequest):
    if not request.query.strip(): raise HTTPException(422,'查询不能为空')
    exact=identify(request.query)
    providers=[exact[0]] if exact else ['arxiv','crossref'] if request.provider=='both' else [request.provider]
    statuses=[]; items=[]
    for provider in providers:
        try:
            result=await search_provider(provider,request.query.strip(),request.mode,request.limit)
            items.extend(result['items']); statuses.append({'provider':provider,'status':'ok','total':result['total'],'cache':result['cache']})
        except httpx.HTTPStatusError as error:
            statuses.append({'provider':provider,'status':'rate_limited' if error.response.status_code==429 else 'not_found' if error.response.status_code==404 else 'failed'})
        except (httpx.HTTPError,ValueError,TypeError,KeyError,ET.ParseError): statuses.append({'provider':provider,'status':'failed'})
    items=list({item['key']:item for item in items}.values())
    payload={'items':decorate(items,request),'providers':statuses,'query':request.query,'created_at':store.now(),
             'warnings':['书目命中不代表论文身份已人工确认；标题、作者、年份和版本需一起核对。元数据不是全文。','未核验代码、实验复现或引用价值；排序仅依据关键词和显式赞踩。']}
    payload['search_id']=repo.save_search(request.model_dump(),payload)
    return payload


@router.get('/literature/records/{key:path}')
def record(key:str):
    item=repo.get_record(key)
    if not item: raise HTTPException(404,'书目记录不存在，请先检索')
    return item


class ImportRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(max_length=250)
    confirm_identity:Literal[True]


async def download_pdf(identity):
    async with _gates['arxiv']:
        if _blocked_until['arxiv']>time.monotonic(): raise ValueError('provider_cooldown')
        delay=3.05-(time.monotonic()-_last['arxiv'])
        if delay>0: await asyncio.sleep(delay)
        _last['arxiv']=time.monotonic()
        return await _download_pdf(identity)


async def _download_pdf(identity):
    if not ARXIV.fullmatch(identity) or not re.search(r'v\d+$',identity): raise ValueError('invalid_arxiv_revision')
    url='https://arxiv.org/pdf/'+identity
    spool=tempfile.SpooledTemporaryFile(max_size=1024*1024,mode='w+b')
    maximum=min(100,max(1,int(os.getenv('SCHOLARTRACE_MAX_UPLOAD_MB','100'))))*1024*1024
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10),follow_redirects=False) as client:
            for attempt in range(4):
                parsed=urlsplit(url)
                if parsed.scheme!='https' or parsed.hostname not in {'arxiv.org','export.arxiv.org'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in {None,443}:
                    raise ValueError('unsafe_pdf_redirect')
                if unquote(parsed.path) not in {'/pdf/'+identity,'/pdf/'+identity+'.pdf'}: raise ValueError('pdf_identity_changed')
                async with client.stream('GET',url,headers={'User-Agent':'ScholarTrace/0.4 (local academic research assistant)'}) as response:
                    if response.is_redirect:
                        url=urljoin(url,response.headers.get('location',''))
                        continue
                    response.raise_for_status()
                    total=0
                    async for chunk in response.aiter_bytes():
                        total+=len(chunk)
                        if total>maximum: raise ValueError('pdf_too_large')
                        spool.write(chunk)
                    spool.seek(0)
                    if not spool.read(1024).lstrip().startswith(b'%PDF-'): raise ValueError('not_a_pdf')
                    spool.seek(0)
                    return spool,url
            raise ValueError('too_many_redirects')
    except BaseException:
        spool.close(); raise


@router.post('/literature/import')
async def import_paper(body:ImportRequest,request:Request):
    item=repo.get_record(body.key)
    if not item: raise HTTPException(404,'先检索并核对元数据')
    if item['provider']!='arxiv' or not item.get('pdf_url'): raise HTTPException(422,'当前只有元数据；请到出版方核对开放全文后手动导入')
    try:
        stream,url=await download_pdf(item['identifier'])
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'全文获取失败，元数据已保留；可稍后重试或从原站手动下载')
    from .main import _accept_upload
    upload=UploadFile(file=stream,filename=item['identifier'].replace('/','_')+'.pdf')
    try:
        document=await _accept_upload(upload,request)
    except HTTPException as error:
        if error.status_code!=503 or not isinstance(error.detail,dict) or not error.detail.get('document_id'): raise
        document=store.get_document(error.detail['document_id'])
        if not document: raise
    provenance={'key':item['key'],'identifier':item['identifier'],'metadata_title':item['title'],'authors':item['authors'],
                'download_url':url,'metadata_source':item['source_url'],'fetched_at':store.now(),'sha256':document['sha256'],'document_id':document['id']}
    repo.bind_import(document,item['key'],provenance)
    return {'document':document,'provenance':provenance}


@router.get('/documents/{document_id}/provenance')
def provenance(document_id:str):
    if not store.get_document(document_id): raise HTTPException(404,'文献不存在')
    return {'items':repo.imports(document_id)}


class FeedbackRequest(BaseModel):
    key:str=Field(max_length=250)
    value:Literal[-1,0,1]


@router.post('/literature/feedback')
def feedback(body:FeedbackRequest):
    if not repo.get_record(body.key): raise HTTPException(404,'记录不存在')
    repo.set_feedback(body.key,body.value)
    return {'key':body.key,'value':body.value}


class SubscriptionRequest(BaseModel):
    query:str=Field(min_length=2,max_length=300)
    enabled:bool=False


@router.post('/literature/subscriptions')
def subscribe(body:SubscriptionRequest):
    query=body.query.strip()
    if len(query)<2 or identify(query): raise HTTPException(422,'追踪请输入研究主题，而不是单篇标识符')
    previous=next((item for item in repo.subscriptions() if item['query'].casefold()==query.casefold()),None)
    if not previous and len(repo.subscriptions())>=30: raise HTTPException(422,'本地版最多30个追踪主题')
    item=previous or {'id':str(uuid.uuid4()),'query':query,'created_at':store.now(),'last_success_at':None,'last_status':'not_checked','next_check_at':store.now()}
    item['enabled']=body.enabled; repo.save_subscription(item)
    return item


@router.get('/literature/subscriptions')
def subscriptions(): return {'items':repo.subscriptions()}


async def check_subscription(identity):
    item=repo.subscription(identity)
    if not item: raise HTTPException(404,'主题不存在')
    if identity in _checking: raise HTTPException(409,'主题正在检查中')
    _checking.add(identity)
    try:
        try:
            result=await search_provider('arxiv',item['query'],'topic',20,fresh=True,latest=True)
            result['warnings']=['只检查最近更新的20条，超出窗口可能漏检；本地服务运行时才执行周期任务。']
            return repo.record_check(identity,result)
        except httpx.HTTPStatusError as error:
            return repo.record_check(identity,{},'rate_limited' if error.response.status_code==429 else 'failed')
        except (httpx.HTTPError,ValueError,TypeError,KeyError,ET.ParseError): return repo.record_check(identity,{},'failed')
    finally: _checking.discard(identity)


@router.post('/literature/subscriptions/{identity}/check')
async def check(identity:str): return await check_subscription(identity)


@router.get('/literature/subscriptions/{identity}/checks')
def checks(identity:str):
    if not repo.subscription(identity): raise HTTPException(404,'主题不存在')
    return {'items':repo.checks(identity)}


async def tracking_loop():
    while True:
        for item in repo.subscriptions():
            if item['enabled'] and item['next_check_at']<=store.now() and item['id'] not in _checking:
                try: await check_subscription(item['id'])
                except HTTPException: pass
        await asyncio.sleep(60)


def start():
    global _scheduler
    repo.init_db()
    _scheduler=asyncio.create_task(tracking_loop())


async def shutdown():
    global _scheduler
    if _scheduler:
        _scheduler.cancel()
        await asyncio.gather(_scheduler,return_exceptions=True)
        _scheduler=None
