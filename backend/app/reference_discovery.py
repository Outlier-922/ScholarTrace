"""One-hop bibliography discovery with explicit bounded network permission."""
import hashlib
import json
import re
from typing import Annotated
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import graph, literature, literature_store as repo, store

router=APIRouter(prefix='/api',tags=['reference-discovery'])

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parse_run_id:str=Field(min_length=1,max_length=100)
    unit_ids:list[Annotated[str,Field(min_length=1,max_length=250)]]=Field(min_length=1,max_length=5)
    network:Literal['cache_only','allow']='cache_only'
    max_requests:int=Field(default=0,ge=0,le=3)
    queries:dict[str,Annotated[str,Field(min_length=2,max_length=500)]]=Field(default_factory=dict,max_length=5)

def source(document_id,run):
    doc=store.get_document(document_id)
    structure=store.get_structure(document_id,parse_run_id=run)
    if not doc or not structure:raise HTTPException(404,'原文或解析版本不存在')
    parsed=store.get_parse_run(document_id,structure['parse_run_id'])
    if not parsed or parsed['status'] not in {'ready','partial'}:raise HTTPException(409,'请等待原版本解析完成')
    return doc,structure

@router.get('/documents/{document_id}/reference-discovery')
def units(document_id:str,parse_run_id:str):
    doc,structure=source(document_id,parse_run_id)
    return {'document_id':doc['id'],'parse_run_id':structure['parse_run_id'],'units':graph.bibliography_units(structure),
        'notice':'选择至多5条原文书目，默认只查缓存；联网须明确预算，不自动确认身份。'}

def local_result(document_id:str,body:Request):
    if len(set(body.unit_ids))!=len(body.unit_ids):raise HTTPException(422,'不能重复选择书目')
    doc,structure=source(document_id,body.parse_run_id)
    by_id={u['id']:u for u in graph.bibliography_units(structure)}
    if not set(body.unit_ids)<=by_id.keys():raise HTTPException(422,'书目不属于所选原文解析版本')
    records=graph.cached_records(); selected={};rows=[]
    for identity in body.unit_ids:
        unit=by_id[identity];identities=graph.identifiers(unit['text'])
        roots={key for key,_ in identities};matches=[]
        for item in records:
            root='arxiv:'+graph.root_identifier(item['identifier']) if item['provider']=='arxiv' else 'doi:'+item['identifier'].casefold()
            if root not in roots:continue
            # Explicit versions in the original must not drift to another cached revision.
            explicit=[raw for key,raw in identities if key==root and literature.ARXIV.fullmatch(raw) and re.search(r'v\d+$',raw)]
            if explicit and item['identifier'] not in explicit:continue
            matches.append(item['key']);selected[item['key']]=item
        rows.append({'unit':unit,'identifiers':[{'root':key,'literal':raw} for key,raw in identities],
            'candidate_keys':matches,'status':'candidates' if matches else 'no_cached_identity' if identities else 'no_literal_identifier'})
    if len(selected)>20:raise HTTPException(422,'缓存版本候选超过20条，请减少所选书目；未保存截断结果')
    request=literature.SearchRequest(query='参考文献候选：'+doc['title'][:200],provider='both',mode='reference',include_disliked=True,limit=20)
    result={'items':literature.decorate(list(selected.values()),request),'providers':[{'provider':'local_registry_cache','status':'ok','cache':'saved_snapshot'}],
        'query':request.query,'created_at':store.now(),'reference_discovery':{'document_id':doc['id'],'sha256':doc['sha256'],
            'parse_run_id':body.parse_run_id,'rows':rows,'network_requests':0,'model_calls':0},
        'warnings':['仅展开本次选择的一层书目；没有递归搜索或获取PDF。','无缓存命中不证明论文不存在。未带版本的arXiv引用可能对应多个缓存版本，需自行核对。','标识符匹配不是标题/作者身份认证，也不是引用价值评价；确认后才能批量入库。']}
    result['reference_discovery']['snapshot_sha256']=hashlib.sha256(json.dumps(result['reference_discovery'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    result['search_id']=repo.save_search(request.model_dump(),result)
    return result


@router.post('/documents/{document_id}/reference-discovery')
async def discover(document_id:str,body:Request):
    if not set(body.queries)<=set(body.unit_ids):raise HTTPException(422,'检索词不属于本次所选书目')
    if body.network=='allow' and body.max_requests<1 or body.network=='cache_only' and (body.max_requests or body.queries):
        raise HTTPException(422,'联网需明确设置1—3次请求预算；本地模式不接受联网参数')
    result=local_result(document_id,body)
    if body.network=='cache_only':return result
    original_id=result.pop('search_id');trace=result['reference_discovery'];trace['local_snapshot_id']=original_id
    trace['max_requests']=body.max_requests;trace['network_mode']='allow';count=0
    selected={i['key']:i for i in result['items']}
    previous=literature.request_guard.get()
    def reserve():
        nonlocal count
        if count>=body.max_requests:raise ValueError('reference_budget_exhausted')
        if previous:previous()
        count+=1
    token=literature.request_guard.set(reserve)
    try:
        for row in trace['rows']:
            if row['candidate_keys'] and row['unit']['id'] not in body.queries:continue
            identities=row['identifiers'];override=body.queries.get(row['unit']['id'])
            if not override and len({i['root'] for i in identities})>1:
                row['lookup']={'status':'ambiguous_identifier','notice':'原文含多个标识符，请明确填写本条检索词'};continue
            query=override or (identities[0]['literal'] if identities else row['unit']['text'][:500])
            exact=literature.identify(query);provider=exact[0] if exact else 'crossref'
            lookup={'query':query,'provider':provider};row['lookup']=lookup
            if count>=body.max_requests:lookup['status']='budget_exhausted';continue
            try:
                found=await literature.search_provider(provider,query,'reference',3)
                matches=found['items'][:3]
                if len(set(selected)|{i['key'] for i in matches})>20:
                    lookup['status']='candidate_limit';continue
                for item in matches:selected[item['key']]=item
                row['candidate_keys']=list(dict.fromkeys([*row['candidate_keys'],*[i['key'] for i in matches]]))
                lookup.update(status='ok' if matches else 'no_results',cache=found.get('cache'))
                if matches:row['status']='candidates'
            except httpx.HTTPStatusError as error:
                lookup['status']='rate_limited' if error.response.status_code==429 else 'not_found' if error.response.status_code==404 else 'failed'
            except (httpx.HTTPError,ValueError,TypeError,KeyError,literature.ET.ParseError):lookup['status']='failed'
    finally:literature.request_guard.reset(token)
    doc,_=source(document_id,body.parse_run_id)
    if doc['sha256']!=trace['sha256']:raise HTTPException(409,'原文身份发生变化；联网结果未保存')
    trace['network_requests']=count;trace.pop('snapshot_sha256',None)
    trace['snapshot_sha256']=hashlib.sha256(json.dumps(trace,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    request=literature.SearchRequest(query=result['query'],provider='both',mode='reference',include_disliked=True,limit=20)
    result['items']=literature.decorate(list(selected.values()),request)
    by_provider={}
    for row in trace['rows']:
        if lookup:=row.get('lookup'):
            if not lookup.get('provider'):continue
            status=lookup['status']
            current=by_provider.setdefault(lookup['provider'],{'provider':lookup['provider'],'status':'ok'})
            if status not in {'ok','no_results'}:current['status']=status
    result['providers']+=list(by_provider.values())
    result['warnings']+=['仅对无缓存或明确改词的所选条目联网，每条至多返回3个候选；失败不自动重试。','无标识符时使用原文条目或明确改词查询Crossref；返回结果只是候选，不自动确认身份。']
    result['created_at']=store.now();result['search_id']=repo.save_search(request.model_dump(),result)
    return result
