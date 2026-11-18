"""Local, immutable handoff receipts; metadata never becomes paper evidence."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from . import literature_store as repo, store, workflow_store


class Context(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(max_length=250)
    search_id:str|None=None
    parent_task_id:str|None=None


class LocalRequest(Context):
    document_id:str
    parse_run_id:str
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    confirm_identity:Literal[True]


def metadata(context):
    if context.parent_task_id and not context.search_id:raise HTTPException(422,'父任务交接需指定检索快照')
    if context.search_id:
        saved=repo.get_search(context.search_id)
        if not saved:raise HTTPException(404,'检索快照不存在')
        item=next((item for item in saved['result']['items'] if item['key']==context.key),None)
        if not item:raise HTTPException(422,'书目不属于此快照的已采用候选')
        if saved['result'].get('screening'):
            from .reference_screening import handoff_summary
            handoff_summary(context.search_id,context.key)
    else:item=repo.get_record(context.key)
    if not item:raise HTTPException(404,'请先检索并核对书目')
    if context.parent_task_id:
        task=workflow_store.get(context.parent_task_id)
        if not task:raise HTTPException(404,'原研究任务不可用')
        if not any(step['tool']=='discovery' and step.get('result',{} ) and step['result'].get('search_id')==context.search_id for step in task['steps']):
            raise HTTPException(422,'检索快照不属于原任务')
    return item


def matches(item,document):
    if item['provider']=='arxiv':
        identifier=document.get('arxiv_id') or ''
        if not re.search(r'v\d+$',identifier):identifier+=document.get('version_label') or ''
        return identifier==item['identifier']
    norm=lambda text:re.sub(r'[^\w]','',text.casefold())
    return bool(norm(item['title'])) and norm(item['title'])==norm(document['title'])


def candidates(item):
    return [document for document in store.list_documents() if matches(item,document)][:20]


def capture(context,item,document,origin,*,identity_basis='user_confirmed'):
    from .reference_screening import handoff_summary
    screening=handoff_summary(context.search_id,context.key) if context.search_id else None
    key=[context.key,context.search_id,context.parent_task_id,document['id'],document['sha256'],document['parse_run_id']]
    key.append(hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    if screening:key.append(screening['snapshot_sha256'])
    if identity_basis != 'user_confirmed':
        key.append(identity_basis)
    payload={'id':str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(key))), 'created_at':store.now(),
             'key':context.key,'search_id':context.search_id,'parent_task_id':context.parent_task_id,
             'metadata':item,'source':{name:document[name] for name in ('id','title','filename','sha256','parse_run_id')},
             'origin':origin,'identity_basis':identity_basis,'semantic_verified':False}
    if screening:payload['screening']=screening
    return repo.save_handoff(payload)


def resolve(receipt):
    source=receipt['source'];document=store.get_document(source['id'])
    result={**receipt,'document':document,'can_plan':False,'status':'source_unavailable','notice':'全文来源已删除或不可用。'}
    if not document:return result
    if receipt.get('screening'):
        from .reference_screening import handoff_summary
        try:
            screening=handoff_summary(receipt['search_id'],receipt['key'])
            if screening!=receipt['screening']:raise HTTPException(409,'筛选记录发生变化')
        except HTTPException:
            return {**result,'status':'screening_unavailable','notice':'采用依据或源论文引用位置不可用，不能继续此筛选交接；请重新核对。'}
    if document['sha256']!=source['sha256']:
        return {**result,'status':'source_changed','notice':'文件身份已变化，需重新核对。'}
    internal=store.get_internal_document(document['id'])
    try:
        with Path(internal['file_path']).open('rb') as stream:raw_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:raw_hash=None
    if raw_hash!=source['sha256']:return {**result,'status':'source_changed','notice':'原始PDF缺失或哈希变化，不能创建任务。'}
    run=store.get_parse_run(document['id'],source['parse_run_id'])
    if not run:return {**result,'status':'parse_unavailable','notice':'已绑定的解析版本不可用，需重新核对。'}
    if run['status'] in {'queued','processing'}:
        return {**result,'status':'pending','notice':'正在等待已绑定版本解析；可关闭页面，稍后从后续研究中继续。'}
    if run['status'] not in {'ready','partial'}:
        return {**result,'status':'parse_failed','notice':'已绑定版本解析失败；请重试解析，再按新版本重新核对。'}
    with store.connection() as conn:has_blocks=conn.execute('SELECT 1 FROM blocks WHERE document_id=? AND parse_run_id=? LIMIT 1',(document['id'],source['parse_run_id'])).fetchone()
    if not has_blocks:
        return {**result,'status':'parse_unavailable','notice':'已绑定版本没有可检索内容，不能创建任务。'}
    return {**result,'status':'ready','can_plan':True,
            'notice':'已绑定全文可用于新任务。'+('当前文献已有另一解析版本；继续使用本交接的历史版本。' if document['parse_run_id']!=source['parse_run_id'] else '')}


def ready(identity):
    receipt=repo.get_handoff(identity)
    if not receipt:raise HTTPException(404,'全文交接记录不存在')
    resolved=resolve(receipt)
    if not resolved['can_plan']:raise HTTPException(409,resolved['notice'])
    return receipt


def validate_snapshot(snapshot):
    receipt=ready(snapshot['id'])
    if receipt!=snapshot:raise HTTPException(409,'全文交接快照已变化，请重新规划')


def confirm_local(body):
    item=metadata(body);document=store.get_document(body.document_id)
    if not document:raise HTTPException(404,'本地全文不存在')
    if not matches(item,document):raise HTTPException(422,'本地全文不满足确切标识符/版本或标题匹配，不能关联')
    if document['sha256']!=body.sha256 or document['parse_run_id']!=body.parse_run_id:
        raise HTTPException(409,'本地全文已变化，请刷新后重新核对')
    payload={'source':{name:document[name] for name in ('id','sha256','parse_run_id')}}
    # Validate availability before persisting a local confirmation.
    if not resolve(payload)['can_plan']:raise HTTPException(409,'请等待本地全文解析完成后再关联')
    receipt=capture(body,item,document,'confirmed_local')
    return resolve(receipt)
