"""Bounded, reader-confirmed traversal with a shared durable request ledger."""
import asyncio,json,uuid,weakref
from fastapi import APIRouter,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from typing import Annotated
from . import literature,literature_handoff as handoff,reference_discovery,store,graph,literature_store as repo
router=APIRouter(prefix='/api/literature/traversals',tags=['reference-traversal'])
INSTANCE=str(uuid.uuid4());locks=weakref.WeakValueDictionary()
class Create(BaseModel):
    model_config=ConfigDict(extra='forbid')
    handoff_id:str=Field(max_length=100)
    max_depth:int=Field(default=2,ge=1,le=2)
    max_nodes:int=Field(default=5,ge=1,le=5)
    max_requests:int=Field(default=0,ge=0,le=3)
class Version(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0)
class Expand(Version):
    node_id:str=Field(max_length=100)
    unit_ids:list[Annotated[str,Field(min_length=1,max_length=250)]]=Field(min_length=1,max_length=5)
class Attach(Version):
    parent_node_id:str=Field(max_length=100)
    handoff_id:str=Field(max_length=100)
def require(identity):
    with store.connection() as c:r=c.execute('SELECT payload FROM literature_traversals WHERE id=?',(identity,)).fetchone()
    if not r:raise HTTPException(404,'展开计划不存在')
    return json.loads(r['payload'])
def save(v):
    old=v['revision'];v['revision']+=1
    with store.connection() as c:
        if c.execute('UPDATE literature_traversals SET revision=?,payload=? WHERE id=? AND revision=?',(v['revision'],json.dumps(v,ensure_ascii=False),v['id'],old)).rowcount!=1:raise HTTPException(409,'计划已更新，请刷新')
def view(v):
    for n in v['nodes']:
        if n['status']=='running' and n.get('owner')!=INSTANCE:n['status']='interrupted'
        n['attachable']=[{'id':r['id'],'title':r['source']['title']} for r in repo.list_handoffs() if n.get('search_id') and belongs(r,n['search_id'])]
    return v
def belongs(receipt,search_id):
    if receipt['search_id']==search_id:return True
    saved=repo.get_search(receipt['search_id']) if receipt.get('screening') else None
    return bool(saved and saved['result'].get('screening',{}).get('origin_search_id')==search_id)
def check(v,revision):
    if v['revision']!=revision:raise HTTPException(409,'计划已更新，请刷新；没有重复执行')
    for n in v['nodes']:handoff.ready(n['handoff_id'])
def node(receipt,parent=None):
    doc=store.get_document(receipt['source']['id'])
    return {'id':str(uuid.uuid4()),'handoff_id':receipt['id'],'parent_node_id':parent['id'] if parent else None,'depth':parent['depth']+1 if parent else 0,
        'document_id':doc['id'],'parse_run_id':receipt['source']['parse_run_id'],'sha256':receipt['source']['sha256'],'title':doc['title'],'work_key':graph.doc_key(doc),'status':'ready','attempts':0}
@router.post('')
def create(body:Create):
    receipt=handoff.ready(body.handoff_id)
    v={'id':str(uuid.uuid4()),'revision':0,'created_at':store.now(),'limits':body.model_dump(exclude={'handoff_id'}),'requests_used':0,'nodes':[node(receipt)],'semantic_verified':False,
        'notice':'每层只展开明确选中的书目；下一层须先核对并关联全文。计划内最多两层、五篇、三次书目请求，均不自动获取PDF或调用模型。'}
    with store.connection() as c:c.execute('INSERT INTO literature_traversals VALUES(?,?,?,?)',(v['id'],0,json.dumps(v,ensure_ascii=False),v['created_at']))
    return v
@router.get('')
def history():
    with store.connection() as c:rows=c.execute('SELECT payload FROM literature_traversals ORDER BY created_at DESC LIMIT 20').fetchall()
    return {'items':[{'id':v['id'],'created_at':v['created_at'],'title':v['nodes'][0]['title']} for r in rows if (v:=json.loads(r['payload']))]}
@router.get('/{identity}')
def detail(identity:str):return view(require(identity))
@router.get('/{identity}/export')
def export(identity:str):
    v=view(require(identity))
    v['search_snapshots']={n['search_id']:repo.get_search(n['search_id']) for n in v['nodes'] if n.get('search_id')}
    v['handoffs']={n['handoff_id']:repo.get_handoff(n['handoff_id']) for n in v['nodes']}
    return JSONResponse(v,headers={'Content-Disposition':'attachment; filename="reference-traversal.json"'})
@router.post('/{identity}/attach')
async def attach(identity:str,body:Attach):
    lock=locks.setdefault(identity,asyncio.Lock())
    async with lock:
        v=require(identity);check(v,body.revision)
        parent=next((n for n in v['nodes'] if n['id']==body.parent_node_id),None)
        if not parent or not parent.get('search_id'):raise HTTPException(422,'请先完成父节点书目展开')
        receipt=handoff.ready(body.handoff_id)
        if not belongs(receipt,parent['search_id']):raise HTTPException(422,'交接不属于此父节点展开的候选快照')
        child=node(receipt,parent)
        if child['depth']>v['limits']['max_depth'] or len(v['nodes'])>=v['limits']['max_nodes']:raise HTTPException(409,'已达到计划层数或文献数上限')
        if child['work_key'] in {n['work_key'] for n in v['nodes']}:raise HTTPException(409,'该作品已在计划中，已阻止循环或跨版本重复展开')
        v['nodes'].append(child);save(v);return view(v)
@router.post('/{identity}/expand')
async def expand(identity:str,body:Expand):
    lock=locks.setdefault(identity,asyncio.Lock())
    async with lock:
        v=require(identity);check(v,body.revision)
        n=next((n for n in v['nodes'] if n['id']==body.node_id),None)
        if not n:raise HTTPException(422,'节点不属于此计划')
        if n['depth']>=v['limits']['max_depth']:raise HTTPException(409,'已到计划层数上限')
        if n['status']=='done':return view(v)
        if n['status']=='running' and n.get('owner')==INSTANCE:raise HTTPException(409,'当前节点仍在执行')
        if n['attempts']>=2:raise HTTPException(409,'此节点已达到两次执行上限，首次结果保留')
        if n.get('unit_ids') and n['unit_ids']!=body.unit_ids:raise HTTPException(409,'重试须保留原书目选择')
        available=reference_discovery.units(n['document_id'],n['parse_run_id'])['units']
        if len(set(body.unit_ids))!=len(body.unit_ids) or not set(body.unit_ids)<={u['id'] for u in available}:raise HTTPException(422,'书目不属于此节点解析版本或存在重复')
        by_id={u['id']:u for u in available}
        n.update(status='running',owner=INSTANCE,attempts=n['attempts']+1,unit_ids=body.unit_ids,selected_units=[by_id[i] for i in body.unit_ids]);save(v)
        previous=literature.request_guard.get()
        def reserve():
            if v['requests_used']>=v['limits']['max_requests']:raise ValueError('traversal_budget_exhausted')
            if previous:previous()
            v['requests_used']+=1;save(v)
        token=literature.request_guard.set(reserve)
        try:
            remaining=v['limits']['max_requests']-v['requests_used']
            result=await reference_discovery.discover(n['document_id'],reference_discovery.Request(parse_run_id=n['parse_run_id'],unit_ids=body.unit_ids,network='allow' if remaining else 'cache_only',max_requests=remaining))
            check(v,v['revision']);n.update(status='done',search_id=result['search_id'],result_requests=result['reference_discovery']['network_requests']);save(v)
        except (Exception,asyncio.CancelledError) as error:
            n.update(status='failed',error=error.detail if isinstance(error,HTTPException) and isinstance(error.detail,str) else '节点执行中断或失败；已用请求保留，可明确重试一次。');save(v)
            if isinstance(error,asyncio.CancelledError):raise
        finally:literature.request_guard.reset(token)
        return view(v)
