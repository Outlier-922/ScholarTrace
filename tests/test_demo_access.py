"""Guard tests use isolated data and fictional credentials, with no paid calls."""
import asyncio
import base64
from dataclasses import replace
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from backend.app import demo_access as demo


@pytest.fixture
def demo_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('SCHOLARTRACE_DEMO_MODE', 'true')
    monkeypatch.setenv('SCHOLARTRACE_DEMO_ORIGIN', 'https://demo.example.test')
    monkeypatch.setenv('SCHOLARTRACE_DEMO_USERNAME', 'synthetic-reviewer')
    monkeypatch.setenv('SCHOLARTRACE_DEMO_PASSWORD_HASH', demo.password_hash('synthetic-only-password'))
    monkeypatch.setenv('SCHOLARTRACE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCHOLARTRACE_DEMO_ALLOW_WRITES', 'true')
    return demo.Settings.from_env()


def authorization(password='synthetic-only-password'):
    return {'Authorization': 'Basic ' + base64.b64encode(('synthetic-reviewer:' + password).encode()).decode()}


def application(settings):
    app = FastAPI()
    @app.get('/api/documents')
    def documents():
        return {'items': ['SYNTHETIC private document']}
    @app.post('/api/echo')
    async def echo(request: Request):
        return {'bytes': len(await request.body())}
    @app.post('/api/files')
    async def files(request: Request):
        form = await request.form()
        return {'fields': len(form)}
    @app.get('/pdf')
    def pdf():
        return StreamingResponse(iter([b'first', b'second']), media_type='application/pdf')
    app.add_middleware(demo.DemoGuard, settings=settings)
    return app


def client(settings):
    return TestClient(application(settings), base_url=settings.origin)


@pytest.mark.parametrize('path', ['/', '/api/documents', '/pdf', '/docs', '/openapi.json'])
def test_every_surface_challenges_before_reading(demo_settings, path):
    response = client(demo_settings).get(path)
    assert response.status_code == 401 and response.headers['www-authenticate'].startswith('Basic ')
    assert 'SYNTHETIC private' not in response.text


def test_health_is_minimal_and_public(demo_settings):
    response = client(demo_settings).get('/healthz')
    assert response.json() == {'status': 'ok'}


@pytest.mark.parametrize('value', ['Bearer abc', 'Basic !!!!', 'Basic ' + base64.b64encode(b'user:no').decode(), 'Basic ' + base64.b64encode(b'\xff:pass').decode()])
def test_bad_credentials_fail_closed(demo_settings, value):
    assert client(demo_settings).get('/api/documents', headers={'Authorization': value}).status_code == 401


def test_readonly_and_origin_controls(demo_settings):
    readonly = client(replace(demo_settings, writes=False))
    assert readonly.get('/api/documents', headers=authorization()).status_code == 200
    assert readonly.post('/api/echo', headers=authorization()).status_code == 403
    api = client(demo_settings)
    assert api.post('/api/echo', headers=authorization()).status_code == 403
    assert api.post('/api/echo', headers={**authorization(), 'Origin': 'https://evil.example'}).status_code == 403
    assert api.post('/api/echo', headers={**authorization(), 'Origin': demo_settings.origin}).status_code == 200
    assert api.post('/api/echo', headers={**authorization(), 'X-ScholarTrace-Client': 'api'}).status_code == 200
    assert api.get('/api/documents', headers={**authorization(), 'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_host_and_hidden_files_rejected(demo_settings):
    api = client(demo_settings)
    assert api.get('/api/documents', headers={**authorization(), 'Host': 'other.example'}).status_code == 421
    assert api.get('/api/documents', headers={**authorization(), 'Host': 'demo.example.test/path'}).status_code == 421
    assert api.get('/.env', headers=authorization()).status_code == 404


def test_stream_headers_and_pdf_body_preserved(demo_settings):
    response = client(demo_settings).get('/pdf', headers=authorization())
    assert response.content == b'firstsecond'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-frame-options'] == 'DENY'


def test_login_and_request_limits(demo_settings):
    api = client(replace(demo_settings, failed_logins_per_minute=3))
    for _ in range(3):
        assert api.get('/pdf', headers=authorization('wrong')).status_code == 401
    assert api.get('/pdf', headers=authorization()).status_code == 429
    api = client(replace(demo_settings, requests_per_minute=2))
    for _ in range(2):
        assert api.get('/pdf', headers=authorization()).status_code == 200
    assert api.get('/pdf', headers=authorization()).status_code == 429


def test_declared_and_chunked_body_caps(demo_settings):
    api = client(replace(demo_settings, body_bytes=12))
    headers = {**authorization(), 'Origin': demo_settings.origin}
    assert api.post('/api/echo', content=b'x' * 13, headers=headers).status_code == 413
    assert api.post('/api/echo', content=iter([b'a' * 8, b'b' * 8]), headers=headers).status_code == 413
    assert api.post('/api/files', files={'file': ('sample.pdf', b'%PDF-SYNTHETIC')}, headers=headers).status_code == 413


def test_mutation_concurrency_slot_released(demo_settings):
    started, release = asyncio.Event(), asyncio.Event()
    async def downstream(scope, receive, send):
        started.set(); await release.wait()
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    guard = demo.DemoGuard(downstream, replace(demo_settings, max_mutations=1))
    scope = {'type': 'http', 'path': '/api/echo', 'method': 'POST', 'scheme': 'https',
             'headers': [(b'host', b'demo.example.test'), (b'origin', demo_settings.origin.encode()),
                         (b'authorization', authorization()['Authorization'].encode())]}
    async def run():
        output = []
        async def send(message): output.append(message)
        first = asyncio.create_task(guard(scope, None, send))
        await started.wait()
        await guard(scope, None, send)
        assert any(message.get('status') == 429 for message in output)
        release.set(); await first
        assert guard.active_mutations == 0
    asyncio.run(run())


@pytest.mark.parametrize('name,value', [('SCHOLARTRACE_DEMO_ORIGIN', 'http://public.example'), ('SCHOLARTRACE_DEMO_PASSWORD_HASH', ''), ('SCHOLARTRACE_DEMO_USERNAME', 'u:p'), ('SCHOLARTRACE_DATA_DIR', '')])
def test_incomplete_demo_configuration_fails(demo_settings, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError): demo.Settings.from_env()


def test_bad_mode_not_silently_local(monkeypatch):
    monkeypatch.setenv('SCHOLARTRACE_DEMO_MODE', 'typo')
    with pytest.raises(ValueError, match='invalid_demo_mode'): demo.enabled()


def test_persistent_model_quota_and_token_reservations(demo_settings, monkeypatch):
    monkeypatch.setenv('SCHOLARTRACE_DEMO_MODEL_CALLS_PER_HOUR', '2')
    monkeypatch.setenv('SCHOLARTRACE_DEMO_MODEL_TOKENS_PER_HOUR', '10')
    demo.reserve_model(6)
    with pytest.raises(ValueError, match='demo_model_budget'): demo.reserve_model(5)
    demo.reserve_model(4)
    with pytest.raises(ValueError, match='demo_model_budget'): demo.reserve_model(1)
    # Module reload / new DB connections do not reset the current hour's quota.
    import importlib
    with pytest.raises(ValueError, match='demo_model_budget'): importlib.reload(demo).reserve_model(1)


def test_demo_default_quota_stops_before_provider_request(demo_settings, monkeypatch):
    from backend.app import model_client
    for key, value in {'LLM_BASE_URL':'https://provider.example/v1', 'LLM_MODEL':'synthetic', 'LLM_API_KEY':'synthetic-only', 'SCHOLARTRACE_DEMO_MODEL_CALLS_PER_HOUR':'0'}.items():
        monkeypatch.setenv(key, value)
    def forbidden(*args, **kwargs): raise AssertionError('must not reach provider')
    monkeypatch.setattr(model_client.httpx, 'AsyncClient', forbidden)
    with pytest.raises(ValueError, match='demo_model_budget'):
        asyncio.run(model_client.chat_json('SYNTHETIC', {}, max_tokens=10))


def test_local_mode_has_no_new_model_reservation(monkeypatch):
    monkeypatch.setenv('SCHOLARTRACE_DEMO_MODE', 'false')
    demo.reserve_model(100)
