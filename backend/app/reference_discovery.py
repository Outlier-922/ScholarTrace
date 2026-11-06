"""One-hop cached identifier discovery, pinned to original bibliography units."""
import hashlib
import json
import re
from typing import Annotated
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import graph, literature, literature_store as repo, store

router=APIRouter(prefix='/api',tags=['reference-discovery'])

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parse_run_id:str=Field(min_length=1,max_length=100)
    unit_ids:list[Annotated[str,Field(min_length=1,max_length=250)]]=Field(min_length=1,max_length=5)

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
        'notice':'选择至多5条原文书目，仅按标识符查找已保存注册记录；不联网，不自动确认身份。'}

@router.post('/documents/{document_id}/reference-discovery')
def discover(document_id:str,body:Request):
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
