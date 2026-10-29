"""Explicit translation scope, real client metering through synthetic providers."""
import asyncio
import copy
import io
import json
import threading
import uuid
import zipfile

import httpx
import pytest

from backend.app import workflow, workflow_translation, store
from test_workflow import papers, planned, start, wait, configure


def spec_for(papers, count=1):
    passages = []
    for paper in papers:
        structure = store.get_structure(paper['id'], parse_run_id=paper['parse_run_id'])
        for block in structure['blocks']:
            if block['type'] in {'paragraph', 'text'} and len(block['text'].strip()) >= 10:
                passages.append({'document_id': paper['id'], 'parse_run_id': paper['parse_run_id'], 'block_id': block['id'], 'page': block['page']})
                break
    assert len(passages) >= count
    return {'passages': passages[:count], 'target_language': 'zh', 'glossary': {}}


def provider(monkeypatch, fail=False):
    configure(monkeypatch); requests = []
    monkeypatch.setenv('LLM_MODEL', 'workflow-translation-fixture-' + uuid.uuid4().hex)
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            body = kwargs['json']; requests.append(body)
            if fail: raise httpx.ReadTimeout('synthetic private error', request=httpx.Request('POST', url))
            payload = json.loads(body['messages'][-1]['content'])
            # Deliberate fixture output, never a claim of real translation quality.
            text = '合成译文：' + payload['text']
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'translation': text})}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}, request=httpx.Request('POST', url))
    monkeypatch.setattr(workflow.model_client.httpx, 'AsyncClient', Provider)
    return requests


def test_translation_requires_explicit_source_before_any_planner_call(client, papers, monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError('No request before scope validation')
    monkeypatch.setattr(workflow.model_client, 'chat_json', forbidden)
    response = client.post('/api/research-tasks', json={'goal': '翻译这些论文', 'document_ids': [p['id'] for p in papers], 'planner': 'model'})
    assert response.status_code == 422 and '段落' in response.text


@pytest.mark.parametrize('kind', ['outside_document', 'wrong_run', 'wrong_page', 'duplicate', 'too_many', 'glossary'])
def test_bad_translation_scope_or_parameters_are_rejected(client, papers, kind):
    spec = spec_for(papers)
    if kind == 'outside_document': spec['passages'][0]['document_id'] = 'not-selected'
    if kind == 'wrong_run': spec['passages'][0]['parse_run_id'] = 'wrong-run'
    if kind == 'wrong_page': spec['passages'][0]['page'] = 999
    if kind == 'duplicate': spec['passages'] *= 2
    if kind == 'too_many': spec['passages'] *= 5
    if kind == 'glossary': spec['glossary'] = {'': 'invalid'}
    response = client.post('/api/research-tasks', json={'goal': '翻译指定段落', 'actions': ['translation'], 'translation': spec, 'document_ids': [p['id'] for p in papers]})
    assert response.status_code in {409, 422}


def test_offline_translation_does_not_masquerade_as_a_translation(client, papers):
    task = planned(client, papers, goal='翻译指定段落', actions=['translation'], translation=spec_for(papers))
    assert task['translation_passages'][0]['text']
    assert [step['tool'] for step in task['steps']] == ['evidence', 'translation']
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and result['usage']['model']['calls'] == 0
    translated = result['steps'][1]['result']
    assert translated['translation'] is None and translated['translation_status'] == 'not_translated'
    assert any('尚未生成译文' in gap for gap in result['gaps'])


def test_one_request_per_paragraph_cached_replay_and_export(client, papers, monkeypatch):
    requests = provider(monkeypatch)
    options = dict(goal='翻译指定段落', actions=['translation'], translation=spec_for(papers, 2), use_model=True)
    task = planned(client, papers, **options); start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 2
    assert result['usage']['model']['calls'] == 2 and result['usage']['model']['reserved_output_tokens'] == 7000
    assert result['usage']['model']['input_tokens'] == 200
    assert all(s['result']['translation_status'] == 'translated' for s in result['steps'][1:])
    assert all(s['result']['semantic_verified'] is False for s in result['steps'][1:])
    exported = client.get('/api/research-tasks/' + task['id'] + '/export')
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert {'translations/translation-1.json', 'translations/translation-2.md'} <= set(archive.namelist())
        assert json.loads(archive.read('translations/translation-1.json'))['evidence'][0] == task['translation_passages'][0]
    replay = planned(client, papers, **options); start(client, replay); replayed = wait(client, replay['id'])
    assert len(requests) == 2 and replayed['usage']['model']['calls'] == 0
    assert all(s['result']['translation_status'] == 'cached' for s in replayed['steps'][1:])


def test_budget_exhausted_translation_stays_pending_then_resumes(client, papers, monkeypatch):
    requests = provider(monkeypatch)
    task = planned(client, papers, goal='翻译指定段落', actions=['translation'], translation=spec_for(papers), use_model=True, max_output_tokens=600)
    start(client, task); pending = wait(client, task['id'])
    assert pending['status'] == 'budget_exhausted' and pending['steps'][1]['status'] == 'waiting'
    assert not requests and pending['usage']['model']['calls'] == 0
    assert client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'resume', 'max_output_tokens': 4000}).status_code == 200
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 1
    assert result['steps'][0]['attempts'] == 1


def test_translation_failure_keeps_failure_and_untranslated_source(client, papers, monkeypatch):
    requests = provider(monkeypatch, fail=True)
    task = planned(client, papers, goal='翻译指定段落', actions=['translation'], translation=spec_for(papers), use_model=True)
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 1
    assert result['steps'][1]['status'] == 'failed' and result['steps'][1]['recovery_status'] == 'local_completed'
    assert result['steps'][2]['tool'] == 'translation'
    assert result['steps'][2]['result']['translation'] is None
    assert result['execution_model_disabled'] and result['plan_version'] == 2
    assert 'synthetic private error' not in json.dumps(result)


def test_changed_original_is_rejected_before_translation_call(client, papers, monkeypatch):
    task = planned(client, papers, goal='翻译指定段落', actions=['translation'], translation=spec_for(papers))
    original = store.get_structure
    def changed(*args, **kwargs):
        result = copy.deepcopy(original(*args, **kwargs))
        for block in result['blocks']:
            if block['id'] == task['translation_passages'][0]['block_id']: block['text'] += ' tampered'
        return result
    monkeypatch.setattr(store, 'get_structure', changed)
    with pytest.raises(ValueError, match='translation_source_changed'):
        asyncio.run(workflow_translation.execute({**task, 'use_model': True}, task['steps'][1]))


def test_model_cannot_add_translation_without_explicit_passages(client, papers, monkeypatch):
    configure(monkeypatch)
    async def proposed(*args, **kwargs):
        return {'actions': ['translation'], 'dimensions': ['hardware']}
    monkeypatch.setattr(workflow.model_client, 'chat_json', proposed)
    task = planned(client, papers, goal='比较硬件', planner='model')
    assert 'translation' not in [step['tool'] for step in task['steps']]
    assert task['translation_passages'] == [] and any('明确原文' in warning for warning in task['warnings'])


def test_pause_at_paragraph_boundary_does_not_retranslate_completed_paragraph(client, papers, monkeypatch):
    requests = provider(monkeypatch); entered = threading.Event(); release = threading.Event()
    original = workflow_translation.execute
    async def delayed(payload, step):
        result = await original(payload, step)
        if step['translation_index'] == 0:
            entered.set()
            while not release.is_set(): await asyncio.sleep(.01)
        return result
    monkeypatch.setattr(workflow_translation, 'execute', delayed)
    task = planned(client, papers, goal='翻译指定段落', actions=['translation'], translation=spec_for(papers, 2), use_model=True)
    start(client, task); assert entered.wait(3)
    assert client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'pause'}).status_code == 200
    release.set(); paused = wait(client, task['id'])
    assert paused['status'] == 'paused' and len(requests) == 1
    assert paused['steps'][1]['status'] == 'completed' and paused['steps'][2]['status'] == 'waiting'
    assert client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'resume'}).status_code == 200
    final = wait(client, task['id'])
    assert len(requests) == 2 and final['steps'][1]['attempts'] == 1
