"""Explicit research-use decisions anchored to the citing paper, never a quality score."""
import copy
import hashlib
import json
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from . import graph,reading,reference_discovery,literature_store as repo,store

router=APIRouter(prefix='/api/literature/searches',tags=['reference-screening'])

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(min_length=1,max_length=250)
    unit_id:str=Field(min_length=1,max_length=250)
    identity:Literal['confirmed','mismatch','uncertain']
    decision:Literal['include','exclude','hold']
    purpose:Literal['method','data','comparison','background','unknown']='unknown'
    reason:str=Field(min_length=8,max_length=800)
    citation_id:str|None=Field(default=None,max_length=250)
    quote:str=Field(default='',max_length=800)

class ScreenRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    goal:str=Field(min_length=4,max_length=800)
    decisions:list[Decision]=Field(min_length=1,max_length=20)

def context(identity):
    saved=repo.get_search(identity)
    if not saved:raise HTTPException(404,'检索快照不存在')
    value=saved['result'];trace=value.get('reference_discovery')
    if not trace:raise HTTPException(422,'请先从原文参考文献展开候选')
    doc,structure=reference_discovery.source(trace['document_id'],trace['parse_run_id'])
    if doc['sha256']!=trace['sha256']:raise HTTPException(409,'源文件身份已变化')
    internal=store.get_internal_document(doc['id'])
    try:
        with Path(internal['file_path']).open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:checksum=None
    if checksum!=trace['sha256']:raise HTTPException(409,'原PDF缺失或内容变化，不能核对引用用途')
    units={u['id']:u for u in graph.bibliography_units(structure)}
    numeric=reading.citation_map(doc['id'],trace['parse_run_id'])
    backlinks={r['id']:r['backlinks'] for r in numeric['references']}
    authors=graph.author_key_backlinks(structure,list(units.values()),trace['parse_run_id'])
    blocks={b['id']:b for b in structure['blocks']};rows=[]
    for row in trace['rows']:
        unit=units.get(row['unit']['id'])
        if unit!=row['unit']:raise HTTPException(409,'书目原块与保存快照不一致')
        links={l['id']:l for rid in unit['reference_ids'] for l in backlinks.get(rid,[])}
        links.update({l['id']:l for l in authors.get(unit['id'],[])})
        rows.append({**row,'body_citations':[{**l,'text':blocks[l['block_id']]['text'],'block_type':blocks[l['block_id']]['type']} for l in links.values()]})
    return saved,{'document_id':doc['id'],'parse_run_id':trace['parse_run_id'],'rows':rows,'items':value['items'],
        'notice':'正文位置仅说明源论文在此引用，不证明候选身份正确或适合你的研究。没有解析到正文位置时保留待查。'}

@router.get('/{identity}/screening-context')
def detail(identity:str):return context(identity)[1]

def handoff_summary(identity,key):
    saved=repo.get_search(identity)
    if not saved or not saved['result'].get('screening'):return None
    saved,ctx=context(identity);value=saved['result'];screening=value['screening']
    decision=next((d for d in screening['decisions'] if d['key']==key),None)
    if not decision or decision['decision']!='include' or decision['identity']!='confirmed' or key not in {i['key'] for i in value['items']}:
        raise HTTPException(409,'筛选采用范围与全文交接不一致')
    row=next((r for r in ctx['rows'] if r['unit']['id']==decision['unit_id'] and key in r['candidate_keys']),None)
    anchor=decision.get('anchor')
    link=next((l for l in row['body_citations'] if anchor and l['id']==anchor['id']),None) if row else None
    if not link or any(anchor[k]!=link[k] for k in ('block_id','page','bbox','text','offset','marker_text')):
        raise HTTPException(409,'筛选引用位置不可用或已变化')
    start,end=anchor['quote_start'],anchor['quote_end']
    if link['text'][start:end]!=anchor['quote'] or not start<=link['offset'] or end<link['offset']+len(link['marker_text']):
        raise HTTPException(409,'筛选引用原句范围不一致')
    trace=value['reference_discovery']
    return {'search_id':identity,'snapshot_sha256':hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'goal':screening['goal'],'decision':copy.deepcopy(decision),
        'citing_source':{k:trace[k] for k in ('document_id','parse_run_id','sha256')},'semantic_verified':False}

@router.get('/{identity}/screenings')
def history(identity:str):
    if not repo.get_search(identity):raise HTTPException(404,'检索快照不存在')
    with store.connection() as conn:
        rows=conn.execute("SELECT id,payload,created_at FROM literature_searches WHERE json_extract(payload,'$.screening.origin_search_id')=? ORDER BY created_at DESC LIMIT 20",(identity,)).fetchall()
    return {'items':[{'id':r['id'],'created_at':r['created_at'],'goal':json.loads(r['payload'])['screening']['goal']} for r in rows]}

@router.post('/{identity}/screen')
def screen(identity:str,body:ScreenRequest):
    saved,ctx=context(identity);value=saved['result']
    if value.get('screening'):raise HTTPException(422,'请打开原检索快照另存新的筛选，不对筛选结果嵌套改写')
    if not body.goal.strip():raise HTTPException(422,'请填写具体研究目标')
    by_key={i['key']:i for i in value['items']}
    if len({d.key for d in body.decisions})!=len(body.decisions) or {d.key for d in body.decisions}!=by_key.keys():
        raise HTTPException(422,'须逐项处理原快照中的全部候选，不能重复或漏掉')
    rows={r['unit']['id']:r for r in ctx['rows']};decisions=[]
    for d in body.decisions:
        row=rows.get(d.unit_id)
        if not row or d.key not in row['candidate_keys']:raise HTTPException(422,'候选与所选原文书目不对应')
        if len(d.reason.strip())<8:raise HTTPException(422,'请具体说明采用、排除或待查依据')
        anchor=None
        if d.citation_id:
            link=next((l for l in row['body_citations'] if l['id']==d.citation_id),None)
            if not link or len(d.quote.strip())<8 or link['text'].count(d.quote)!=1:
                raise HTTPException(422,'引用片段须在此条目关联的正文原块中唯一连续出现')
            start=link['text'].index(d.quote);end=start+len(d.quote)
            if not start<=link['offset'] or end<link['offset']+len(link['marker_text']):
                raise HTTPException(422,'引用片段必须覆盖本条目的实际正文引用标记')
            anchor={**link,'quote':d.quote,'quote_start':start,'quote_end':end}
        elif d.quote:raise HTTPException(422,'请先选择引用位置再填写原句')
        if d.decision=='include' and (d.identity!='confirmed' or d.purpose=='unknown' or not anchor):
            raise HTTPException(422,'采用需确认候选身份、明确用途并附带覆盖引用标记的原句；证据不足请保留待查')
        decisions.append({**d.model_dump(),'reason':d.reason.strip(),'anchor':anchor})
    result=copy.deepcopy(value);result.pop('search_id',None)
    adopted={d['key'] for d in decisions if d['decision']=='include'}
    result['items']=[i for i in result['items'] if i['key'] in adopted]
    result['screening']={'origin_search_id':identity,'origin_sha256':hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'goal':body.goal.strip(),'decisions':decisions,'candidate_metadata':value['items'],'created_at':store.now(),
        'basis':'explicit_reader_judgment','semantic_verified':False}
    result['warnings']+=['这是读者针对所填研究目标的用途判断，不是自动引用价值评分或独立学术质量认证。','排除及待查项保留在筛选轨迹，不进入此快照的入库候选；原快照完整保留。']
    result['created_at']=store.now()
    result['search_id']=repo.save_search(saved['request'],result)
    return result

@router.get('/{identity}/screening-export')
def export(identity:str):
    saved=repo.get_search(identity)
    if not saved or not saved['result'].get('screening'):raise HTTPException(404,'筛选记录不存在')
    return JSONResponse(saved,headers={'Content-Disposition':'attachment; filename="reference-screening.json"'})
