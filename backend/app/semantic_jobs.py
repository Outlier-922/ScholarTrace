"""Explicit local indexing jobs; cached batches survive interruption, no downloads."""
from concurrent.futures import ThreadPoolExecutor
import threading
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from . import store, semantic, qa, source_scope

router=APIRouter(prefix='/api',tags=['semantic-index'])
_executor=None
_stop=threading.Event()
_lifecycle=threading.RLock()


class IndexRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:str=Field(min_length=1,max_length=100)
    parse_run_id:str=Field(min_length=1,max_length=100)


def init():
    global _executor
    with _lifecycle:
        _stop.clear()
        _executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='semantic-index')
        with store.connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS semantic_index_jobs(
                id TEXT PRIMARY KEY,document_id TEXT NOT NULL,parse_run_id TEXT NOT NULL,
                status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT,report TEXT)''')
            conn.execute("UPDATE semantic_index_jobs SET status='interrupted',error='服务中断；可重新准备，复用已有缓存。',updated_at=? WHERE status IN ('queued','running')",(store.now(),))


def update(identity,**values):
    values['updated_at']=store.now()
    with store.connection() as conn:
        conn.execute('UPDATE semantic_index_jobs SET '+','.join(key+'=?' for key in values)+' WHERE id=?',(*values.values(),identity))


def get(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT * FROM semantic_index_jobs WHERE id=?',(identity,)).fetchone()
    if not row or not store.get_document(row['document_id']):return None
    result=dict(row);result['report']=json.loads(result['report']) if result['report'] else None
    return result


def execute(identity,documents):
    try:
        def progress(done,total):
            if _stop.is_set():raise InterruptedError()
            if not store.get_document(documents[0]['id']):raise InterruptedError()
            update(identity,status='running',done=done,total=total)
        if _stop.is_set():raise InterruptedError()
        sources=qa._collect_sources(documents)
        if not sources:raise ValueError('empty_sources')
        _,report=semantic.search('research evidence',sources,force_index=True,progress=progress)
        if not report.get('used'):raise ValueError('model_not_ready')
        if _stop.is_set():raise InterruptedError()
        update(identity,status='completed',done=report['indexed_chunks'],total=report['indexed_chunks'],
               report=json.dumps(report,ensure_ascii=False),error=None)
    except InterruptedError:
        update(identity,status='interrupted',error='准备中断；已有缓存保留，可重新准备。')
    except Exception:
        # Never echo model paths, exception bodies or credentials.
        update(identity,status='failed',error='本地索引准备失败；已有缓存保留，请检查本地模型与来源。')


@router.post('/semantic-index-jobs',status_code=202)
def create(body:IndexRequest):
    document=store.get_document(body.document_id)
    if not document:raise HTTPException(404,'文献不存在或已删除')
    documents=source_scope.pin([document],{body.document_id:body.parse_run_id})
    if store.get_parse_run(body.document_id,body.parse_run_id)['status'] not in {'ready','partial'}:
        raise HTTPException(409,'请等待所选解析版本完成后再准备索引。')
    status=semantic.runtime_status()
    if not status['ready'] or not status['enabled']:
        raise HTTPException(409,'请先准备并启用本地语义模型；此入口不会下载模型。')
    with _lifecycle:
        if _executor is None or _stop.is_set():raise HTTPException(503,'索引服务正在停止')
        with store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active=conn.execute("SELECT id,document_id,parse_run_id FROM semantic_index_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
            if active:
                if active['document_id']==body.document_id and active['parse_run_id']==body.parse_run_id:
                    return get(active['id'])
                raise HTTPException(409,'另一个文献正在准备索引，请等待完成后再试。')
            identity=store.new_id('index');now=store.now()
            conn.execute('INSERT INTO semantic_index_jobs(id,document_id,parse_run_id,status,created_at,updated_at) VALUES(?,?,?,\'queued\',?,?)',
                         (identity,body.document_id,body.parse_run_id,now,now))
        try:_executor.submit(execute,identity,documents)
        except RuntimeError:
            update(identity,status='interrupted',error='索引服务正在停止，可稍后重试。')
            raise HTTPException(503,'索引服务正在停止')
    return get(identity)


@router.get('/semantic-index-jobs/{identity}')
def read(identity:str):
    result=get(identity)
    if not result:raise HTTPException(404,'索引任务或来源不可用')
    return result


async def shutdown():
    global _executor
    with _lifecycle:
        _stop.set();executor=_executor;_executor=None
    if executor:await run_in_threadpool(lambda:executor.shutdown(wait=True,cancel_futures=True))
