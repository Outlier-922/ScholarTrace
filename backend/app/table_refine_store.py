import json
import re
from . import store


def init_db():
    with store.connection() as c:c.execute('CREATE TABLE IF NOT EXISTS table_refinements(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)')


def image_path(identity):
    if not re.fullmatch('[0-9a-f-]{36}',identity):raise ValueError('invalid_table_identity')
    folder=store.data_dir()/'table_previews';folder.mkdir(exist_ok=True)
    return folder/(identity+'.png')


def valid(value):
    doc=store.get_document(value['document_id'])
    if not doc or doc['sha256']!=value['sha256']:return False
    structure=store.get_structure(doc['id'],page=value['page'],parse_run_id=value['parse_run_id'])
    if not structure:return False
    page=next((p for p in structure['pages'] if p['number']==value['page']),None)
    if not page or any(page[k]!=value['page_geometry'][k] for k in ('width','height','rotation','cropbox')):return False
    anchor=value.get('anchor')
    return not anchor or any(b==anchor for b in structure['blocks'])


def save(value):
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if not valid(value):raise ValueError('table_source_changed')
        c.execute('INSERT INTO table_refinements VALUES(?,?,?,?,?)',(value['id'],value['document_id'],value['kind'],json.dumps(value,ensure_ascii=False),value['created_at']))


def get(identity):
    with store.connection() as c:row=c.execute('SELECT payload FROM table_refinements WHERE id=?',(identity,)).fetchone()
    if not row:return None
    value=json.loads(row['payload']);return value if valid(value) else None


def history(document_id,kind):
    with store.connection() as c:rows=c.execute('SELECT payload FROM table_refinements WHERE document_id=? AND kind=? ORDER BY created_at DESC LIMIT 100',(document_id,kind)).fetchall()
    return [v for r in rows if valid(v:=json.loads(r['payload']))]


def purge_document(identity):
    with store.connection() as c:
        ids=[r[0] for r in c.execute("SELECT id FROM table_refinements WHERE document_id=? AND kind='source'",(identity,))]
        c.execute('DELETE FROM table_refinements WHERE document_id=?',(identity,))
    for source_id in ids:image_path(source_id).unlink(missing_ok=True)
