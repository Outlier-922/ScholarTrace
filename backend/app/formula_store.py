import json
import re
from . import store


def init_db():
    with store.connection() as c:
        c.execute('CREATE TABLE IF NOT EXISTS formula_artifacts(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)')


def image_path(identity, *, continued=False):
    if not re.fullmatch(r'[0-9a-f-]{36}',identity): raise ValueError('invalid_formula_id')
    folder=store.data_dir()/'formula_previews';folder.mkdir(exist_ok=True)
    return folder/(identity+('-continued.png' if continued else '.png'))


def _valid_region(region):
    return isinstance(region,list) and len(region)==4 and 0<=region[0]<region[2]<=1 and 0<=region[1]<region[3]<=1


def valid(payload):
    doc=store.get_document(payload['document_id'])
    if not doc or doc['sha256']!=payload['sha256']:return False
    structure=store.get_structure(doc['id'],page=payload['page'],parse_run_id=payload['parse_run_id'])
    if not structure or not any(page['number']==payload['page'] for page in structure['pages']):
        return False
    if not payload['blocks']:
        if not _valid_region(payload.get('region')):
            return False
        continuation=payload.get('continuation')
        return not continuation or (
            continuation['page']==payload['page']+1 and _valid_region(continuation.get('region'))
            and any(page['number']==continuation['page'] for page in structure['pages'])
        )
    blocks={b['id']:b for b in (structure or {}).get('blocks',[])}
    return all(b['id'] in blocks and all(b[k]==blocks[b['id']][k] for k in ('text','bbox','page','type','metadata')) for b in payload['blocks'])


def save(payload):
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if not valid(payload):raise ValueError('formula_source_changed')
        c.execute('INSERT INTO formula_artifacts VALUES(?,?,?,?,?)',(payload['id'],payload['document_id'],payload['kind'],json.dumps(payload,ensure_ascii=False),payload['created_at']))


def get(identity):
    with store.connection() as c:row=c.execute('SELECT payload FROM formula_artifacts WHERE id=?',(identity,)).fetchone()
    if not row:return None
    value=json.loads(row['payload']);return value if valid(value) else None


def history(document_id,kind):
    with store.connection() as c:rows=c.execute('SELECT payload FROM formula_artifacts WHERE document_id=? AND kind=? ORDER BY created_at DESC LIMIT 100',(document_id,kind)).fetchall()
    return [p for row in rows if valid(p:=json.loads(row['payload']))]


def purge_document(identity):
    with store.connection() as c:
        ids=[r[0] for r in c.execute("SELECT id FROM formula_artifacts WHERE document_id=? AND kind='source'",(identity,))]
        c.execute('DELETE FROM formula_artifacts WHERE document_id=?',(identity,))
    for source_id in ids:
        image_path(source_id).unlink(missing_ok=True)
        image_path(source_id,continued=True).unlink(missing_ok=True)
