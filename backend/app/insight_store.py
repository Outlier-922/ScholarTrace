"""Saved generated insights remain bound to every selected source snapshot."""
import json
from . import store


def init_db():
    with store.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS research_insights(id TEXT PRIMARY KEY,graph_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)')


def valid(conn, payload):
    for source in payload['sources']:
        row = conn.execute('SELECT sha256 FROM documents WHERE id=? AND deleted=0', (source['document_id'],)).fetchone()
        if not row or row['sha256'] != source['sha256']:
            return False
        if not conn.execute('SELECT 1 FROM parse_runs WHERE id=? AND document_id=?', (source['parse_run_id'], source['document_id'])).fetchone():
            return False
    for item in payload['evidence']:
        row = conn.execute('SELECT text,page,bbox FROM blocks WHERE id=? AND document_id=? AND parse_run_id=?', (item['block_id'], item['document_id'], item['parse_run_id'])).fetchone()
        if not row or row['text'] != item['text'] or row['page'] != item['page'] or json.loads(row['bbox']) != item['bbox']:
            return False
    return True


def save(payload):
    init_db()
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not valid(conn, payload):
            raise ValueError('insight_source_unavailable')
        conn.execute('INSERT INTO research_insights VALUES(?,?,?,?,?)', (payload['id'], payload['graph_id'], payload['kind'], json.dumps(payload, ensure_ascii=False), payload['created_at']))


def get(identity):
    init_db()
    with store.connection() as conn:
        row = conn.execute('SELECT payload FROM research_insights WHERE id=?', (identity,)).fetchone()
        if not row:
            return None
        payload = json.loads(row['payload'])
        return payload if valid(conn, payload) else None


def history(graph_id, kind):
    init_db()
    with store.connection() as conn:
        rows = conn.execute('SELECT payload FROM research_insights WHERE graph_id=? AND kind=? ORDER BY created_at DESC LIMIT 30', (graph_id, kind)).fetchall()
        return [payload for row in rows if valid(conn, payload := json.loads(row['payload']))]


def purge_document(document_id):
    init_db()
    with store.connection() as conn:
        for row in conn.execute('SELECT id,payload FROM research_insights').fetchall():
            if any(source['document_id'] == document_id for source in json.loads(row['payload'])['sources']):
                conn.execute('DELETE FROM research_insights WHERE id=?', (row['id'],))
