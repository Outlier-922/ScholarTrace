"""Immutable source-version graph snapshots with explicit user annotations."""
from __future__ import annotations
import json
from . import store


def init_db():
    with store.connection() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS research_graphs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS graph_followups(id TEXT PRIMARY KEY,graph_id TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);''')


def require_sources(conn,payload):
    for source in payload['sources']:
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND deleted=0',(source['document_id'],)).fetchone():
            raise ValueError('source_deleted')
        if not conn.execute('SELECT 1 FROM parse_runs WHERE id=? AND document_id=?',(source['parse_run_id'],source['document_id'])).fetchone():
            raise ValueError('source_run_missing')


def save(payload):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE');require_sources(conn,payload)
        conn.execute('INSERT INTO research_graphs VALUES(?,?,?)',(payload['id'],json.dumps(payload,ensure_ascii=False),store.now()))


def get(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM research_graphs WHERE id=?',(identity,)).fetchone()
        if not row:return None
        payload=json.loads(row['payload'])
        try:require_sources(conn,payload)
        except ValueError:return None
        return payload


def list_graphs():
    with store.connection() as conn:
        rows=conn.execute('SELECT id,payload,created_at FROM research_graphs ORDER BY created_at DESC LIMIT 30').fetchall()
        result=[]
        for row in rows:
            payload=json.loads(row['payload'])
            try:require_sources(conn,payload)
            except ValueError:continue
            result.append({'id':row['id'],'created_at':row['created_at'],'node_count':len(payload['nodes']),'edge_count':len(payload['edges']),'topic':payload['topic']})
        return result


def annotate(identity,edge_id,purpose):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT payload FROM research_graphs WHERE id=?',(identity,)).fetchone()
        if not row:return None
        payload=json.loads(row['payload']);require_sources(conn,payload)
        edge=next((edge for edge in payload['edges'] if edge['id']==edge_id),None)
        if not edge:return None
        edge['purpose']=purpose;edge['purpose_status']='user_labeled' if purpose!='unknown' else 'unclassified'
        conn.execute('UPDATE research_graphs SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),identity))
        return payload


def save_followup(identity,payload):
    graph=get(identity)
    if not graph:raise ValueError('source_deleted')
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE');require_sources(conn,graph)
        conn.execute('INSERT INTO graph_followups VALUES(?,?,?,?)',(payload['id'],identity,json.dumps(payload,ensure_ascii=False),store.now()))


def followups(identity):
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM graph_followups WHERE graph_id=? ORDER BY created_at DESC LIMIT 20',(identity,))]


def purge_document(document_id):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute('SELECT id,payload FROM research_graphs').fetchall():
            if any(item['document_id']==document_id for item in json.loads(row['payload'])['sources']):
                conn.execute('DELETE FROM graph_followups WHERE graph_id=?',(row['id'],))
                conn.execute('DELETE FROM research_graphs WHERE id=?',(row['id'],))
