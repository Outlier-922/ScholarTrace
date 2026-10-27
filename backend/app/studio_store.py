"""Local, source-aware studio assets and optimistic draft revisions."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from . import store


def init_db():
    with store.connection() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS studio_assets(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS studio_revisions(asset_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(asset_id,revision));''')


def folder(identity):
    if str(uuid.UUID(identity))!=identity:raise ValueError('invalid_asset_id')
    base=(store.data_dir()/'studio').resolve();base.mkdir(exist_ok=True)
    path=(base/identity).resolve()
    if not path.is_relative_to(base):raise ValueError('invalid_asset_path')
    return path


def require_sources(conn,payload):
    for source in payload.get('sources',[]):
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND deleted=0',(source['document_id'],)).fetchone():raise ValueError('source_deleted')
        if not conn.execute('SELECT 1 FROM parse_runs WHERE id=? AND document_id=?',(source['parse_run_id'],source['document_id'])).fetchone():raise ValueError('source_parse_missing')
    if payload.get('dataset_id') and not conn.execute("SELECT 1 FROM studio_assets WHERE id=? AND kind='dataset' AND deleted=0",(payload['dataset_id'],)).fetchone():raise ValueError('dataset_deleted')


def create(payload):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE');require_sources(conn,payload)
        conn.execute('INSERT INTO studio_assets(id,kind,payload,created_at) VALUES(?,?,?,?)',(payload['id'],payload['kind'],json.dumps(payload,ensure_ascii=False),payload['created_at']))
        if payload['kind']=='draft':conn.execute('INSERT INTO studio_revisions VALUES(?,?,?,?)',(payload['id'],payload['revision'],json.dumps(payload,ensure_ascii=False),store.now()))


def get(identity,revision=None):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM studio_assets WHERE id=? AND deleted=0',(identity,)).fetchone()
        if not row:return None
        payload=json.loads(row['payload'])
        if revision is not None:
            old=conn.execute('SELECT payload FROM studio_revisions WHERE asset_id=? AND revision=?',(identity,revision)).fetchone()
            if not old:return None
            payload=json.loads(old['payload'])
        try:require_sources(conn,payload)
        except ValueError:return None
        return payload


def list_assets(kind=None):
    with store.connection() as conn:
        rows=conn.execute('SELECT payload FROM studio_assets WHERE deleted=0 ORDER BY created_at DESC LIMIT 100').fetchall();result=[]
        for row in rows:
            item=json.loads(row['payload'])
            if kind and item['kind']!=kind:continue
            try:require_sources(conn,item)
            except ValueError:continue
            result.append({key:item.get(key) for key in ['id','kind','title','created_at','revision','dataset_id','sha256']})
        return result


def save_draft(payload,expected):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT payload FROM studio_assets WHERE id=? AND kind='draft' AND deleted=0",(payload['id'],)).fetchone()
        if not row:raise ValueError('draft_missing')
        current=json.loads(row['payload'])
        if current['revision']!=expected:raise ValueError('revision_conflict')
        require_sources(conn,payload)
        payload['revision']=expected+1;payload['updated_at']=store.now()
        conn.execute('UPDATE studio_assets SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),payload['id']))
        conn.execute('INSERT INTO studio_revisions VALUES(?,?,?,?)',(payload['id'],payload['revision'],json.dumps(payload,ensure_ascii=False),store.now()))
    return payload


def revisions(identity):
    with store.connection() as conn:
        return [{'revision':row['revision'],'created_at':row['created_at']} for row in conn.execute('SELECT revision,created_at FROM studio_revisions WHERE asset_id=? ORDER BY revision DESC',(identity,))]


def purge_document(document_id):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute("SELECT id,payload FROM studio_assets WHERE kind='draft' AND deleted=0").fetchall():
            if any(source['document_id']==document_id for source in json.loads(row['payload']).get('sources',[])):
                conn.execute('DELETE FROM studio_assets WHERE id=?',(row['id'],))
                conn.execute('DELETE FROM studio_revisions WHERE asset_id=?',(row['id'],))
