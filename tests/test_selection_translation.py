"""Exact-source translation contracts with injected, fictional model responses."""
import uuid
import pymupdf
import pytest
from conftest import upload_pdf, wait_for_document
from backend.app import model_client, store


@pytest.fixture
def selection(client, tmp_path, monkeypatch):
    path = tmp_path / 'selection.pdf'
    with pymupdf.open() as pdf:
        pdf.set_metadata({'title': 'SYNTHETIC selection ' + uuid.uuid4().hex})
        page = pdf.new_page()
        page.insert_text((40, 60), 'The fictional batch size is 8 and the context is 256 tokens.')
        page.insert_text((40, 110), 'Another fictional paragraph for wrong-source checks.')
        pdf.save(path)
    doc = wait_for_document(client, upload_pdf(client, path)['id'])
    blocks = client.get(f"/api/documents/{doc['id']}/structure").json()['blocks']
    block = next(b for b in blocks if 'batch size' in b['text'])
    text = 'batch size is 8';start = block['text'].index(text)
    body = {'document_id': doc['id'], 'parse_run_id': doc['parse_run_id'], 'block_id': block['id'],
            'page': 1, 'start': start, 'end': start + len(text), 'source_text': text, 'target_language': 'zh'}
    for key, value in {'LLM_BASE_URL': 'https://fictional.example/v1', 'LLM_MODEL': 'synthetic', 'LLM_API_KEY': 'synthetic-key'}.items():
        monkeypatch.setenv(key, value)
    return body, doc, block


def test_exact_selection_only_sent_and_cached(client, selection, monkeypatch):
    body, doc, block = selection; calls = []
    async def fake(system, data, **kwargs):
        calls.append(data)
        assert '256' not in data['text'] and kwargs['max_tokens'] == 1600
        return {'translation': '合成译文：' + data['text']}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    value = client.post('/api/selection-translations', json=body)
    assert value.status_code == 200, value.text
    result = value.json()
    assert '8' in result['translation'] and result['source']['bbox'] == block['bbox']
    assert result['source']['parse_run_id'] == doc['parse_run_id'] and result['semantic_verified'] is False
    assert client.post('/api/selection-translations', json=body).json()['status'] == 'cached'
    assert len(calls) == 1
    monkeypatch.setenv('LLM_API_KEY', '')
    assert client.get('/api/selection-translations/' + result['id']).status_code == 200


@pytest.mark.parametrize('key,value,status', [('source_text', 'invented text', 409), ('parse_run_id', 'wrong', 404), ('block_id', 'wrong', 422), ('page', 2, 422), ('start', -1, 422), ('end', 99999, 422), ('start', True, 422)])
def test_wrong_or_unbounded_source_never_calls_model(client, selection, monkeypatch, key, value, status):
    body, *_ = selection
    async def forbidden(*args, **kwargs): raise AssertionError('No model request for invalid source')
    monkeypatch.setattr(model_client, 'chat_json', forbidden)
    body[key] = value
    assert client.post('/api/selection-translations', json=body).status_code == status


def test_bad_number_or_placeholder_not_saved(client, selection, monkeypatch):
    body, *_ = selection
    async def fake(*args, **kwargs): return {'translation': '合成伪译文 999'}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    response = client.post('/api/selection-translations', json=body)
    assert response.status_code == 502
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) FROM translations WHERE document_id=?', (body['document_id'],)).fetchone()[0] == 0


def test_no_model_is_explicit(client, selection, monkeypatch):
    body, *_ = selection;monkeypatch.setenv('LLM_API_KEY', '')
    assert client.post('/api/selection-translations', json=body).status_code == 503


def test_deleted_source_invalidates_record(client, selection, monkeypatch):
    body, doc, _ = selection
    async def fake(system, data, **kwargs): return {'translation': data['text']}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result = client.post('/api/selection-translations', json=body).json()
    client.delete('/api/documents/' + doc['id'])
    assert client.get('/api/selection-translations/' + result['id']).status_code == 404


def test_code_or_formula_selection_preserved_without_model(client, selection, monkeypatch):
    body, _, _ = selection
    with store.connection() as conn:
        conn.execute("UPDATE blocks SET type='formula_candidate' WHERE id=?", (body['block_id'],))
    monkeypatch.setenv('LLM_API_KEY', '')
    result = client.post('/api/selection-translations', json=body)
    assert result.status_code == 200 and result.json()['status'] == 'preserved'
    assert result.json()['translation'] == body['source_text']


def test_historical_parse_selection_survives_new_parse(client, selection, monkeypatch):
    body, doc, _ = selection
    newer = store.start_reprocess(doc['id'], {'ocr': False, 'tables': False})
    assert newer['parse_run_id'] != body['parse_run_id']
    async def fake(system, data, **kwargs): return {'translation': data['text']}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result = client.post('/api/selection-translations', json=body)
    assert result.status_code == 200 and result.json()['source']['parse_run_id'] == body['parse_run_id']


def test_deletion_during_generation_never_returns_saved_translation(client, selection, monkeypatch):
    body, doc, _ = selection
    async def fake(system, data, **kwargs):
        store.delete_document(doc['id'])
        return {'translation': data['text']}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    assert client.post('/api/selection-translations', json=body).status_code == 404


def test_unicode_offsets_are_codepoints_not_utf16(client, selection, monkeypatch):
    body, _, block = selection
    with store.connection() as conn:
        conn.execute('UPDATE blocks SET text=? WHERE id=?', ('🧪' + block['text'], block['id']))
    body['start'] += 1; body['end'] += 1
    async def fake(system, data, **kwargs): return {'translation': data['text']}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    assert client.post('/api/selection-translations', json=body).status_code == 200
    body['start'] += 1; body['end'] += 1
    assert client.post('/api/selection-translations', json=body).status_code == 409
