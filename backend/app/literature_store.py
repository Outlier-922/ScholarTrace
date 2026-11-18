"""Persistent external metadata, query provenance, feedback and tracking snapshots."""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timedelta, timezone
from . import store


def init_db():
    with store.connection() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS literature_records(key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL,expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_searches(id TEXT PRIMARY KEY,request TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_feedback(key TEXT PRIMARY KEY,value INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS literature_profile_feedback(profile_id TEXT NOT NULL REFERENCES literature_profiles(id),key TEXT NOT NULL,value INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(profile_id,key));
        CREATE TABLE IF NOT EXISTS literature_imports(document_id TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(document_id,key));
        CREATE TABLE IF NOT EXISTS literature_handoffs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_batches(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_traversals(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_subscriptions(id TEXT PRIMARY KEY,query TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS literature_seen(subscription_id TEXT NOT NULL,key TEXT NOT NULL,root_key TEXT NOT NULL,PRIMARY KEY(subscription_id,key));
        CREATE TABLE IF NOT EXISTS literature_checks(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')


def save_records(items):
    with store.connection() as conn:
        for item in items:
            conn.execute('INSERT OR REPLACE INTO literature_records VALUES(?,?,?)',(item['key'],json.dumps(item,ensure_ascii=False),store.now()))


def get_record(key):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM literature_records WHERE key=?',(key,)).fetchone()
        return json.loads(row['payload']) if row else None


def cache_get(key):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM literature_cache WHERE key=? AND expires_at>?',(key,store.now())).fetchone()
        return json.loads(row['payload']) if row else None


def cache_snapshot(key):
    """Read an explicitly labeled local snapshot, including an expired one."""
    with store.connection() as conn:
        row=conn.execute('SELECT payload,expires_at FROM literature_cache WHERE key=?',(key,)).fetchone()
        return {'payload':json.loads(row['payload']),'expires_at':row['expires_at'],'expired':row['expires_at']<=store.now()} if row else None


def cache_put(key,payload):
    expiry=(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat()
    with store.connection() as conn:
        conn.execute('INSERT OR REPLACE INTO literature_cache VALUES(?,?,?)',(key,json.dumps(payload,ensure_ascii=False),expiry))


def save_search(request,payload):
    identity=str(uuid.uuid4())
    with store.connection() as conn:
        conn.execute('INSERT INTO literature_searches VALUES(?,?,?,?)',(identity,json.dumps(request,ensure_ascii=False),json.dumps(payload,ensure_ascii=False),store.now()))
    return identity


def get_search(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT request,payload FROM literature_searches WHERE id=?',(identity,)).fetchone()
        return {'request':json.loads(row['request']),'result':{**json.loads(row['payload']),'search_id':identity}} if row else None


def profiles():
    with store.connection() as conn:
        named = [dict(row) for row in conn.execute('SELECT id,name FROM literature_profiles ORDER BY name_key')]
    return [{'id':'default', 'name':'默认偏好'}, *named]


def profile_exists(identity):
    if identity == 'default':
        return True
    with store.connection() as conn:
        return conn.execute('SELECT 1 FROM literature_profiles WHERE id=?', (identity,)).fetchone() is not None


def save_profile(name):
    key = name.casefold()
    with store.connection() as conn:
        conn.execute('INSERT OR IGNORE INTO literature_profiles VALUES(?,?,?)', (str(uuid.uuid4()), name, key))
        return dict(conn.execute('SELECT id,name FROM literature_profiles WHERE name_key=?', (key,)).fetchone())


def feedback(profile_id='default'):
    with store.connection() as conn:
        if profile_id == 'default':
            rows = conn.execute('SELECT key,value FROM literature_feedback')
        else:
            rows = conn.execute('SELECT key,value FROM literature_profile_feedback WHERE profile_id=?', (profile_id,))
        return {row['key']:row['value'] for row in rows}


def set_feedback(key,value,profile_id='default'):
    with store.connection() as conn:
        if profile_id == 'default':
            conn.execute('INSERT OR REPLACE INTO literature_feedback VALUES(?,?,?)',(key,value,store.now()))
        else:
            conn.execute('INSERT OR REPLACE INTO literature_profile_feedback VALUES(?,?,?,?)',
                         (profile_id,key,value,store.now()))


def bind_import(document,key,provenance):
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND deleted=0',(document['id'],)).fetchone():
            raise ValueError('source_deleted')
        conn.execute('INSERT OR REPLACE INTO literature_imports VALUES(?,?,?)',(document['id'],key,json.dumps(provenance,ensure_ascii=False)))


def imports(document_id):
    if not store.get_document(document_id): return []
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_imports WHERE document_id=?',(document_id,))]


def save_handoff(payload):
    with store.connection() as conn:
        conn.execute('INSERT OR IGNORE INTO literature_handoffs VALUES(?,?,?)',
                     (payload['id'],json.dumps(payload,ensure_ascii=False),payload['created_at']))
    return get_handoff(payload['id'])


def get_handoff(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM literature_handoffs WHERE id=?',(identity,)).fetchone()
        return json.loads(row['payload']) if row else None


def list_handoffs():
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_handoffs ORDER BY created_at DESC LIMIT 40')]


def batch_get(identity):
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM literature_batches WHERE id=?',(identity,)).fetchone()
        return json.loads(row['payload']) if row else None


def batch_list():
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_batches ORDER BY created_at DESC LIMIT 20')]


def batch_insert(value):
    with store.connection() as conn:
        conn.execute('INSERT INTO literature_batches VALUES(?,?,?,?)',(value['id'],value['revision'],json.dumps(value,ensure_ascii=False),value['created_at']))


def batch_replace(value,expected_revision):
    with store.connection() as conn:
        return conn.execute('UPDATE literature_batches SET revision=?,payload=? WHERE id=? AND revision=?',
            (value['revision'],json.dumps(value,ensure_ascii=False),value['id'],expected_revision)).rowcount==1


def matching_handoffs(key,search_id,parent_task_id):
    with store.connection() as conn:
        rows=conn.execute("SELECT payload FROM literature_handoffs WHERE json_extract(payload,'$.key')=? AND json_extract(payload,'$.search_id') IS ? AND json_extract(payload,'$.parent_task_id') IS ? ORDER BY created_at DESC LIMIT 20",(key,search_id,parent_task_id)).fetchall()
        return [json.loads(row['payload']) for row in rows]


def purge_document(document_id):
    with store.connection() as conn:
        conn.execute('DELETE FROM literature_imports WHERE document_id=?',(document_id,))


def subscriptions():
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_subscriptions ORDER BY rowid DESC')]


def subscription(identity):
    return next((item for item in subscriptions() if item['id']==identity),None)


def save_subscription(item):
    with store.connection() as conn:
        conn.execute('INSERT OR REPLACE INTO literature_subscriptions VALUES(?,?,?,?)',(item['id'],item['query'].casefold(),int(item['enabled']),json.dumps(item,ensure_ascii=False)))


def record_check(identity,result,error=None):
    """Success snapshot and seen IDs commit atomically; failures never advance baseline."""
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT payload FROM literature_subscriptions WHERE id=?',(identity,)).fetchone()
        if not row: return None
        item=json.loads(row['payload'])
        now=store.now()
        previous={row['key']:row['root_key'] for row in conn.execute('SELECT key,root_key FROM literature_seen WHERE subscription_id=?',(identity,))}
        baseline=item.get('last_success_at') is None
        additions=[]
        if error is None:
            roots=set(previous.values())
            for record in result['items']:
                root=record.get('root_key',record['key'])
                if record['key'] not in previous:
                    additions.append({**record,'change':'baseline' if baseline else 'new_version' if root in roots else 'new_work'})
                    conn.execute('INSERT OR IGNORE INTO literature_seen VALUES(?,?,?)',(identity,record['key'],root))
            item['last_success_at']=now
        check={'id':str(uuid.uuid4()),'subscription_id':identity,'created_at':now,'status':error or ('baseline' if baseline else 'updated' if additions else 'no_change'),
               'items':additions,'warnings':result.get('warnings',[]),'window':'最近更新的至多20条；非完整收割'}
        if error is None:
            check['window_items'] = result['items']
        item.update(last_checked_at=now,last_status=check['status'],next_check_at=(datetime.now(timezone.utc)+timedelta(hours=1 if error else 24)).isoformat())
        conn.execute('UPDATE literature_subscriptions SET payload=? WHERE id=?',(json.dumps(item,ensure_ascii=False),identity))
        conn.execute('INSERT INTO literature_checks VALUES(?,?,?,?)',(check['id'],identity,json.dumps(check,ensure_ascii=False),now))
        return {k: v for k, v in check.items() if k != 'window_items'}


def checks(identity):
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_checks WHERE subscription_id=? ORDER BY created_at DESC LIMIT 20',(identity,))]


def latest_checked_window(identity):
    with store.connection() as conn:
        row = conn.execute("""SELECT payload FROM literature_checks
                          WHERE subscription_id=? AND json_type(payload,'$.window_items')='array'
                          ORDER BY created_at DESC LIMIT 1""", (identity,)).fetchone()
        return json.loads(row['payload']) if row else None
