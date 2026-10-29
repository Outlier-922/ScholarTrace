"""Explicit metadata queries; candidates never expand a task's full-text sources."""
import xml.etree.ElementTree as ET
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import literature, literature_store, store, workflow_store


class DiscoverySpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=2,max_length=500)
    provider:Literal['arxiv','crossref','both']='both'
    mode:Literal['topic','title','reference']='title'
    author:str=Field(default='',max_length=120)
    year:int|None=Field(default=None,ge=1600,le=2100)
    limit:int=Field(default=3,ge=1,le=5)
    network:Literal['cache_only','allow']='cache_only'


class MetadataBudgetExceeded(Exception):
    pass


class MetadataFailure(Exception):
    def __init__(self,result):
        self.partial_result=result
        self.error_code=result['provider_status']['status']
        super().__init__('metadata_provider_failed')


def prepare(spec):
    if not spec.query.strip():raise HTTPException(422,'请输入明确的检索词、标题或标识符')
    spec=spec.model_copy(update={'query':spec.query.strip(),'author':spec.author.strip()})
    exact=literature.identify(spec.query)
    providers=[exact[0]] if exact else ['arxiv','crossref'] if spec.provider=='both' else [spec.provider]
    return spec,providers


def reserve(identity):
    def change(payload):
        usage=payload['usage']['metadata']
        if usage['requests']>=payload['budgets'].get('max_metadata_requests',0):
            raise MetadataBudgetExceeded('metadata_budget_exhausted')
        usage['requests']+=1
    workflow_store.mutate(identity,change)


async def execute(payload,step,cache_only=False):
    spec=DiscoverySpec(**payload['discovery_spec'])
    provider=step['metadata_provider']
    offline=cache_only or spec.network=='cache_only' or payload.get('execution_metadata_disabled',False)
    request=literature.SearchRequest(**{**spec.model_dump(exclude={'network'}),'provider':provider})
    status={'provider':provider,'status':'ok'};raw=[]
    def authorize_request():
        if offline:raise ValueError('metadata_network_disabled')
        reserve(payload['id'])
    token=literature.request_guard.set(authorize_request)
    try:
        result=await literature.search_provider(provider,spec.query,spec.mode,spec.limit,cache_only=offline)
        raw=list({item['key']:item for item in result['items']}.values())[:spec.limit]
        status.update(cache=result['cache'],fetched_at=result.get('fetched_at'),cache_expires_at=result.get('cache_expires_at'),total=result['total'])
    except literature.MetadataCacheMiss:
        status['status']='cache_miss'
    except httpx.HTTPStatusError as error:
        status['status']='rate_limited' if error.response.status_code==429 else 'not_found' if error.response.status_code==404 else 'failed'
    except (httpx.HTTPError,ValueError,TypeError,KeyError,ET.ParseError):
        status['status']='failed'
    finally:
        literature.request_guard.reset(token)
    selected=[];excluded=[]
    for item in literature.decorate(raw,request):
        reasons=[]
        if spec.author and item['identity_checks']['author_match'] is not True:reasons.append('作者未匹配指定字面条件')
        if spec.year and item['identity_checks']['year_match'] is not True:reasons.append('年份不匹配或缺失')
        if reasons:excluded.append({'record':item,'reasons':reasons})
        else:
            if spec.author:item['reasons'].append('满足指定作者字面条件')
            if spec.year:item['reasons'].append('年份匹配：'+str(spec.year))
            selected.append(item)
    result={'id':step['artifact_id'],'mode':'metadata_candidates','query':spec.query,'items':selected,'excluded_candidates':excluded,
            'providers':[status],'provider_status':status,'created_at':store.now(),'network_mode':'cache_only' if offline else 'allow',
            'fulltext_imported':False,'semantic_verified':False,
            'warnings':['书目候选需核对身份、年份与版本；元数据不能作为全文结论。','排序沿用关键词和显式反馈，不代表引用价值或论文质量。','确认入库后请新建后续任务，当前任务的原文来源保持固定。']}
    if status.get('cache')=='stale':result['warnings'].append('此处是过期缓存快照，未重新联网，不能据此判断最新状态。')
    if status['status']!='ok':result['warnings'].append('本次书目状态：'+status['status']+'；未返回候选不证明论文不存在。')
    result['search_id']=literature_store.save_search(request.model_dump(),result)
    if status.get('cache') in {'hit','stale'}:
        workflow_store.mutate(payload['id'],lambda value:value['usage']['metadata'].update(cache_hits=value['usage']['metadata']['cache_hits']+1))
    if status['status'] in {'failed','rate_limited'} and not offline:
        raise MetadataFailure(result)
    return result


def markdown(result):
    lines=['# 任务文献发现快照','',result['query'],'','状态：'+result['provider_status']['status']+'；仅书目元数据。','']
    for item in result['items']:
        lines += ['## '+item['title'],f"{item['identifier']} / {item.get('year')} / {', '.join(item['authors'])}",item['source_url'],*item['reasons'],'']
    lines += ['## 未采用候选','']
    lines += [item['record']['title']+'：'+'；'.join(item['reasons']) for item in result['excluded_candidates']]
    lines += ['','## 待核对','',*result['warnings']]
    return '\n\n'.join(lines)
