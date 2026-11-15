"""Atomic document-translation plans and small per-block checkpoints."""
import json
from . import store


def init_db():
    with store.connection() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS document_translation_jobs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_translation_units(job_id TEXT NOT NULL,sequence INTEGER NOT NULL,block_id TEXT NOT NULL,page INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job_id,block_id));
        CREATE INDEX IF NOT EXISTS document_translation_pending ON document_translation_units(job_id,status,sequence);''')


def _live(conn,document_id):
    if not conn.execute('SELECT 1 FROM documents WHERE id=? AND deleted=0',(document_id,)).fetchone():raise ValueError('source_unavailable')


def create(job,units):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE');_live(conn,job['document_id'])
        conn.execute('INSERT INTO document_translation_jobs VALUES(?,?,?,?)',(job['id'],job['document_id'],json.dumps(job,ensure_ascii=False),job['created_at']))
        conn.executemany('INSERT INTO document_translation_units VALUES(?,?,?,?,?,?)',
            [(job['id'],index,unit['block_id'],unit['page'],unit['status'],json.dumps(unit,ensure_ascii=False)) for index,unit in enumerate(units)])


def get(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT document_id,payload FROM document_translation_jobs WHERE id=?',(identity,)).fetchone()
        if not row:return None
        try:_live(conn,row['document_id'])
        except ValueError:return None
        return json.loads(row['payload'])


def items(identity,page=None,pending=False,limit=None):
    sql='SELECT payload FROM document_translation_units WHERE job_id=?';args=[identity]
    if page is not None:sql+=' AND page=?';args.append(page)
    if pending:sql+=" AND status IN ('pending','running','failed')"
    sql+=' ORDER BY sequence'
    if limit is not None:sql+=' LIMIT ?';args.append(limit)
    with store.connection() as conn:return [json.loads(row[0]) for row in conn.execute(sql,args)]


def item(identity,block_id):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM document_translation_units WHERE job_id=? AND block_id=?',(identity,block_id)).fetchone()
        return json.loads(row[0]) if row else None


def mutate(identity,change,expected=None,unit=None):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT document_id,payload FROM document_translation_jobs WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('job_unavailable')
        _live(conn,row['document_id']);job=json.loads(row['payload'])
        if expected is not None and job['revision']!=expected:raise ValueError('revision_conflict')
        change(job)
        if unit is not None:
            cursor=conn.execute('UPDATE document_translation_units SET status=?,payload=? WHERE job_id=? AND block_id=?',
                (unit['status'],json.dumps(unit,ensure_ascii=False),identity,unit['block_id']))
            if cursor.rowcount!=1:raise ValueError('unit_missing')
        job['counts']={row[0]:row[1] for row in conn.execute('SELECT status,count(*) FROM document_translation_units WHERE job_id=? GROUP BY status',(identity,))}
        job['revision']+=1;job['updated_at']=store.now()
        conn.execute('UPDATE document_translation_jobs SET payload=? WHERE id=?',(json.dumps(job,ensure_ascii=False,allow_nan=False),identity))
        return job


def list_jobs(document_id,run_id=None):
    with store.connection() as conn:
        rows=conn.execute('SELECT id FROM document_translation_jobs WHERE document_id=? ORDER BY created_at DESC LIMIT 40',(document_id,)).fetchall()
    return [job for row in rows if (job:=get(row['id'])) and (run_id is None or job['source']['parse_run_id']==run_id)]


def recover():
    with store.connection() as conn:identities=[row[0] for row in conn.execute('SELECT id FROM document_translation_jobs')]
    for identity in identities:
        if not (job:=get(identity)) or job['status'] not in {'queued','running','pause_requested','cancel_requested'}:continue
        for unit in items(identity,pending=True):
            if unit['status']=='running':
                unit.update(status='pending',interrupted=True);mutate(identity,lambda value:None,unit=unit)
        mutate(identity,lambda value:value.update(status='cancelled' if value.get('control')=='cancel' else 'paused',
            notice='服务已重启，进度保留；需明确开始下一批。中断请求可能已经计费。'))


def purge_document(document_id):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM document_translation_units WHERE job_id IN (SELECT id FROM document_translation_jobs WHERE document_id=?)',(document_id,))
        conn.execute('DELETE FROM document_translation_jobs WHERE document_id=?',(document_id,))
