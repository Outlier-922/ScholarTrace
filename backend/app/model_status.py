"""Read-only configuration and explicit tiny probes; never expose provider bodies."""
import asyncio
import hashlib
import os
import uuid
from fastapi import APIRouter
import httpx
from . import model_client,store

router=APIRouter(prefix='/api/model',tags=['model-readiness'])
_check_lock=asyncio.Lock()


def init_db():
    with store.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS model_checks(id TEXT PRIMARY KEY,model_identity TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)')


def connection_identity():
    return hashlib.sha256((model_client.identity()+'|'+os.getenv('LLM_API_KEY','')).encode()).hexdigest()


def last_check():
    import json
    with store.connection() as conn:
        row=conn.execute('SELECT payload FROM model_checks WHERE model_identity=? ORDER BY created_at DESC LIMIT 1',(connection_identity(),)).fetchone()
        return json.loads(row['payload']) if row else None


@router.get('/status')
def status():
    return {**model_client.configuration(),'last_check':last_check(),'quality_verified':False,
            'notice':'配置齐全不代表连接成功；连接检查不代表问答、翻译或语义支持质量合格。'}


def error_code(error):
    if isinstance(error,httpx.HTTPStatusError):
        code=error.response.status_code
        if code in {401,403}:
            return 'authentication_failed'
        return {402:'payment_required',429:'rate_limited'}.get(code,'provider_error')
    if isinstance(error,httpx.TimeoutException):return 'timeout'
    if isinstance(error,httpx.HTTPError):return 'network_error'
    return 'invalid_output_or_configuration'


@router.post('/check')
async def check():
    import json
    if not model_client.configuration()['ready']:
        return {**status(),'status':'not_ready','documents_sent':False}
    async with _check_lock:
        payload=await probe()
        with store.connection() as conn:
            conn.execute('INSERT INTO model_checks VALUES(?,?,?,?)',(payload['id'],connection_identity(),json.dumps(payload),payload['checked_at']))
        return payload


async def probe():
    payload={'id':str(uuid.uuid4()),'checked_at':store.now(),'status':'failed','documents_sent':False,'quality_verified':False}
    if not model_client.configuration()['ready']:return {**payload,'status':'not_ready'}
    try:
        result=await model_client.chat_json('Output one JSON object: {"status":"ok"}. This is a connection check.',{},max_tokens=600)
        if result.get('status')!='ok':raise ValueError('unexpected_probe_output')
        payload['status']='connected'
    except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError) as error:payload['error_code']=error_code(error)
    return payload
