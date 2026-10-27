"""SQLite checkpoints and atomic control requests for bounded research tasks."""
from __future__ import annotations
import json
from . import store


def init_db():
    with store.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS research_tasks(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL)')


def require_sources(conn,payload):
    for source in payload['sources']:
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND deleted=0',(source['document_id'],)).fetchone():
            raise ValueError('source_unavailable')
        if not conn.execute('SELECT 1 FROM parse_runs WHERE id=? AND document_id=?',(source['parse_run_id'],source['document_id'])).fetchone():
            raise ValueError('source_unavailable')


def event(payload,label,detail=''):
    payload['events'].append({'sequence':payload['revision']+1,'time':store.now(),'label':label,'detail':detail[:500]})
    payload['events']=payload['events'][-200:]


def create(payload):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE');require_sources(conn,payload)
        conn.execute('INSERT INTO research_tasks VALUES(?,?,?)',(payload['id'],json.dumps(payload,ensure_ascii=False,allow_nan=False),payload['created_at']))


def get(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM research_tasks WHERE id=?',(identity,)).fetchone()
        if not row:return None
        payload=json.loads(row['payload'])
        try:require_sources(conn,payload)
        except ValueError:return None
        return payload


def mutate(identity,change,expected_revision=None):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT payload FROM research_tasks WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('task_unavailable')
        payload=json.loads(row['payload']);require_sources(conn,payload)
        if expected_revision is not None and payload['revision']!=expected_revision:raise ValueError('revision_conflict')
        change(payload)
        payload['revision']+=1;payload['updated_at']=store.now()
        conn.execute('UPDATE research_tasks SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,allow_nan=False),identity))
    return payload


def list_tasks():
    with store.connection() as conn:
        rows=conn.execute('SELECT id FROM research_tasks ORDER BY created_at DESC LIMIT 40').fetchall()
    items=[]
    for row in rows:
        if not (payload:=get(row['id'])):continue
        items.append({key:payload[key] for key in ['id','goal','status','created_at','updated_at','revision','planner_mode']})
    return items


def recover():
    # A previous process may have exited after a side effect but before saving
    # its result. Deterministic artifact IDs make the controlled writes reusable.
    with store.connection() as conn:
        candidates=[json.loads(row['payload']) for row in conn.execute('SELECT payload FROM research_tasks')]
    for task in candidates:
        if task['status'] not in {'running','queued','pause_requested','cancel_requested'}:continue
        def interrupted(payload):
            payload['status']='cancelled' if payload['control']=='cancel' else 'paused'
            for step in payload['steps']:
                if step['status']=='running':
                    step['status']='waiting'
                    step['recovered_after_interruption']=True
            event(payload,'服务重启，检查点已恢复','已完成步骤保留；未完成步骤等待继续。中断中的外部模型请求可能已计费，预留预算不退还。')
        try:mutate(task['id'],interrupted)
        except ValueError:pass


def purge_document(document_id):
    with store.connection() as conn:
        for row in conn.execute('SELECT id,payload FROM research_tasks').fetchall():
            if any(source['document_id']==document_id for source in json.loads(row['payload'])['sources']):
                conn.execute('DELETE FROM research_tasks WHERE id=?',(row['id'],))
