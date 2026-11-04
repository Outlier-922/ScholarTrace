"""Generated insights use fictional PDFs and injected responses, never live claims."""
import asyncio
import uuid
import pymupdf
import pytest
from conftest import upload_pdf, wait_for_document
from backend.app import graph, insights, literature_store, model_client, store


@pytest.fixture
def papers(client, tmp_path, monkeypatch):
    docs = []
    for i, text in enumerate(['We plan to explore speech attention in future work using a fictional benchmark.',
                               'We tested speech attention on 8 fictional samples and report limited results.',
                               'The unrelated synthetic method studies image labels only.']):
        path = tmp_path / f'paper-{i}.pdf'
        with pymupdf.open() as pdf:
            pdf.set_metadata({'title': f'SYNTHETIC insight {i} ' + uuid.uuid4().hex})
            page = pdf.new_page(); page.insert_text((40, 50), f'SYNTHETIC insight {i}', fontsize=15)
            page.insert_text((40, 100), text, fontsize=10); pdf.save(path)
        doc = wait_for_document(client, upload_pdf(client, path)['id'])
        identifier = f'{2001 + i * 100:04d}.9999{i}'
        store.update_document(doc['id'], arxiv_id=identifier, version_label='v1')
        doc = store.get_document(doc['id']); docs.append(doc)
        record = {'key': 'arxiv:' + identifier + 'v1', 'provider': 'arxiv', 'identifier': identifier + 'v1',
                  'title': doc['title'], 'authors': ['Fictional Tester'], 'year': 2020 + i,
                  'published': f'{2020+i}-01-01T00:00:00Z', 'updated': f'{2020+i}-01-01T00:00:00Z',
                  'source_url': 'https://example.test/SYNTHETIC-registry', 'landing_url': 'https://example.test/fictional', 'retrieved_at': store.now()}
        literature_store.save_records([record])
        literature_store.bind_import(doc, record['key'], {'key': record['key'], 'identifier': record['identifier'], 'sha256': doc['sha256']})
    value = client.post('/api/graphs', json={'document_ids': [doc['id'] for doc in docs[:2]], 'topic': 'speech attention future work', 'resolve_metadata': False}).json()
    outline = client.get(f"/api/graphs/{value['id']}/outline").json()
    future = next(item for item in outline['future_work'] if item['document_id'] == docs[0]['id'])
    body = {'block_id': future['block_id'], 'direction_text': future['text'], 'candidate_document_ids': [docs[1]['id']]}
    for key, val in {'LLM_BASE_URL':'https://fictional.example/v1', 'LLM_MODEL':'synthetic', 'LLM_API_KEY':'synthetic-only'}.items(): monkeypatch.setenv(key, val)
    return docs, value, body


def claim(items, kind='paper_fact', text=None):
    return {'kind': kind, 'text': text or ' '.join(item['text'] for item in items),
            'evidence_ids': [item['id'] for item in items], 'quotes': [{'evidence_id': item['id'], 'quote': item['text'][:150]} for item in items]}


async def review_response(system, data, **kwargs):
    assert kwargs['max_tokens'] == 2400
    by_doc = {}
    for item in data['evidence']:
        if len(item['text']) > len(by_doc.get(item['document_id'], {}).get('text', '')): by_doc[item['document_id']] = item
    return {'sections': [{'section': 'methods', 'claims': [claim([item]) for item in by_doc.values()]},
                         {'section': 'comparison', 'claims': [claim(list(by_doc.values()), 'synthesis')]}]}


async def future_response(system, data, **kwargs):
    assert kwargs['max_tokens'] == 1800
    original = next(item for item in data['evidence'] if item['document_id'] == data['direction_document_id'])
    result = []
    for identity in data['candidate_document_ids']:
        hits = [item for item in data['evidence'] if item['document_id'] == identity]
        if not hits:
            result.append({'document_id': identity, 'relation': 'insufficient', 'claim': None}); continue
        candidate = max(hits, key=lambda item: len(item['text']))
        result.append({'document_id': identity, 'relation': 'addressed', 'claim': claim([original, candidate], 'synthesis')})
    return {'assessments': result}


def test_review_has_quotes_coverage_history_and_export(client, papers, monkeypatch):
    docs, value, _ = papers; calls = []
    async def fake(*args, **kwargs): calls.append(1); return await review_response(*args, **kwargs)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result = client.post(f"/api/graphs/{value['id']}/reviews", json={})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert len(calls) == 1 and len(payload['sections']) == 2
    assert all(row['status'] == 'has_candidate_claim' for row in payload['coverage'])
    assert payload['semantic_verified'] is False
    assert len(client.get(f"/api/graphs/{value['id']}/insights?kind=review").json()['items']) == 1
    export = client.get(f"/api/research-insights/{payload['id']}/export")
    assert export.status_code == 200 and 'ParseRun:' in export.text


def test_metadata_only_unknown_scope_not_used(client, papers, monkeypatch):
    _, value, _ = papers
    async def forbidden(*args, **kwargs): raise AssertionError('No model for invalid scope')
    monkeypatch.setattr(model_client, 'chat_json', forbidden)
    assert client.post(f"/api/graphs/{value['id']}/reviews", json={'document_ids':['external-metadata-node']}).status_code == 422


def test_invalid_model_rows_leave_coverage_gaps(client, papers, monkeypatch):
    _, value, _ = papers
    async def fake(*args, **kwargs):
        return {'sections': [{'section': [], 'claims': []}, {'section': 'methods', 'claims': [{'kind': [], 'text': 'invalid'}]}]}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    payload = client.post(f"/api/graphs/{value['id']}/reviews", json={}).json()
    assert payload['sections'] == [] and payload['rejected_rows'] == 2
    assert all(row['status'] == 'not_covered' for row in payload['coverage'])


def test_review_uses_attached_quote_for_numbers_and_excludes_paper_wide_omissions(client, papers, monkeypatch):
    docs, graph_result, _ = papers

    async def fake(system, data, **kwargs):
        source = next(item for item in data['evidence'] if '8 fictional samples' in item['text'])
        supported = claim([source], text='作者在 8 个样本上测试语音注意力。')
        short_quote = claim([source], text=supported['text'])
        short_quote['quotes'][0]['quote'] = 'We tested speech attention'
        omission = claim([source], text='作者报告了结果，但原文未进一步讨论其他数据。')
        return {'sections': [{'section': 'limitations', 'claims': [short_quote, omission, supported]}]}

    monkeypatch.setattr(model_client, 'chat_json', fake)
    result = client.post(f"/api/graphs/{graph_result['id']}/reviews", json={'document_ids': [docs[1]['id']]})
    assert result.status_code == 200
    draft = result.json()
    assert draft['rejected_rows'] == 2
    assert draft['review_rejections'] == {'number_outside_quotes': 1, 'paper_scope_omission': 1}
    retained, = draft['sections'][0]['claims']
    assert '8 fictional samples' in retained['quotes'][0]['quote']
    assert retained['semantic_verified'] is False
    export = client.get(f"/api/research-insights/{draft['id']}/export")
    assert '数字未出现在所附引文中 · 1 条' in export.text


def test_review_does_not_use_model_name_digits_as_measurements():
    source = {'id': 'e', 'document_id': 'd', 'text': 'We used GPT-3. Memory was reduced by 3 times.'}
    value = claim([source], text='GPT-3 的内存需求减少 3 倍。')
    assert insights.checked_claim(value, [source], review_rules=True)['text'] == value['text']
    value['quotes'][0]['quote'] = 'We used GPT-3.'
    assert insights.checked_claim(value, [source], review_rules=True) is None


@pytest.mark.parametrize('mutation', ['novel_number', 'fabricated_quote', 'cross_scope', 'one_paper_synthesis'])
def test_claim_checks_reject_specific_failures(mutation):
    evidence = [{'id':'e1','document_id':'a','text':'Fictional result uses 8 examples.'}, {'id':'e2','document_id':'b','text':'Fictional counterpart uses 9 examples.'}]
    value = claim(evidence, 'synthesis')
    if mutation == 'novel_number': value['text'] += ' 999 examples.'
    if mutation == 'fabricated_quote': value['quotes'][0]['quote'] = 'Made up result'
    if mutation == 'cross_scope': value['evidence_ids'].append('unknown')
    if mutation == 'one_paper_synthesis': value = claim(evidence[:1], 'synthesis')
    assert insights.checked_claim(value, evidence) is None


def test_later_exact_version_and_bilateral_quotes_allow_only_provisional_bucket(client, papers, monkeypatch):
    _, value, body = papers; monkeypatch.setattr(model_client, 'chat_json', future_response)
    response = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body)
    assert response.status_code == 200, response.text
    row = response.json()['assessments'][0]
    assert row['bucket'] == 'provisionally_addressed' and row['temporal_status'] == 'later_version_date'
    assert row['semantic_verified'] is False and row['claim']


def test_unknown_version_date_never_excludes_direction(client, papers, monkeypatch):
    docs, value, body = papers; monkeypatch.setattr(model_client, 'chat_json', future_response)
    with store.connection() as conn: conn.execute('DELETE FROM literature_imports WHERE document_id=?', (docs[1]['id'],))
    row = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body).json()['assessments'][0]
    assert row['relation'] == 'addressed' and row['temporal_status'] == 'unknown' and row['bucket'] == 'retain_for_review'


def test_duplicate_or_cross_paper_assessment_not_trusted(client, papers, monkeypatch):
    _, value, body = papers
    async def fake(*args, **kwargs):
        result = await future_response(*args, **kwargs); result['assessments'] *= 2; return result
    monkeypatch.setattr(model_client, 'chat_json', fake)
    row = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body).json()['assessments'][0]
    assert row['relation'] == 'insufficient' and row['claim'] is None


def test_wrong_direction_or_same_work_never_generates(client, papers, monkeypatch):
    docs, value, body = papers
    async def forbidden(*args, **kwargs): raise AssertionError('No invalid model calls')
    monkeypatch.setattr(model_client, 'chat_json', forbidden)
    assert client.post(f"/api/graphs/{value['id']}/future-assessments", json={**body,'direction_text':'Invented direction'}).status_code == 422
    assert client.post(f"/api/graphs/{value['id']}/future-assessments", json={**body,'candidate_document_ids':[docs[0]['id']]}).status_code == 422


def test_candidate_deleted_invalidates_and_purges_saved_assessment(client, papers, monkeypatch):
    docs, value, body = papers; monkeypatch.setattr(model_client, 'chat_json', future_response)
    response = client.post(f"/api/graphs/{value['id']}/future-assessments", json={**body,'candidate_document_ids':[docs[2]['id']]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert client.delete('/api/documents/'+docs[2]['id']).status_code == 200
    assert client.get('/api/research-insights/'+result['id']).status_code == 404
    with store.connection() as conn: assert conn.execute('SELECT count(*) FROM research_insights WHERE id=?',(result['id'],)).fetchone()[0] == 0


def test_no_model_and_provider_error_are_explicit(client, papers, monkeypatch):
    _, value, _ = papers
    monkeypatch.setenv('LLM_API_KEY','')
    assert client.post(f"/api/graphs/{value['id']}/reviews", json={}).status_code == 503
    monkeypatch.setenv('LLM_API_KEY','synthetic')
    async def fake(*args, **kwargs): raise ValueError('synthetic provider failure')
    monkeypatch.setattr(model_client,'chat_json',fake)
    assert client.post(f"/api/graphs/{value['id']}/reviews", json={}).status_code == 502


def test_review_artifact_reused_without_second_generation(client, papers, monkeypatch):
    docs, value, _ = papers; calls = []
    async def fake(*args, **kwargs): calls.append(1); return await review_response(*args, **kwargs)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    identity = str(uuid.uuid4())
    first = asyncio.run(insights.execute_review(value['id'], insights.ReviewRequest(), identity))
    second = asyncio.run(insights.execute_review(value['id'], insights.ReviewRequest(), identity))
    assert first == second and len(calls) == 1
    with pytest.raises(ValueError, match='scope_changed'):
        asyncio.run(insights.execute_review(value['id'], insights.ReviewRequest(document_ids=[docs[0]['id']]), identity))


def test_workflow_infers_review_and_budget_degrades_to_outline(client, papers, monkeypatch):
    from backend.app import workflow
    docs, _, _ = papers
    assert 'review' in workflow.infer('生成文献综述')[0]
    assert 'review' not in workflow.infer('比较结构，不要生成综述')[0]
    task = client.post('/api/research-tasks', json={'goal':'生成文献综述','document_ids':[doc['id'] for doc in docs[:2]],'actions':['review'],'use_model':True,'max_model_calls':0}).json()
    step = next(step for step in task['steps'] if step['tool'] == 'review')
    def forbidden(*args, **kwargs): raise AssertionError('Zero-budget workflow must not reach provider')
    monkeypatch.setattr(model_client.httpx, 'AsyncClient', forbidden)
    budget = model_client.CallBudget(0, 2400)
    with model_client.metered(budget):
        result = asyncio.run(workflow.execute(task, step))
    assert result['mode'] == 'evidence_only' and budget.exhausted
    workflow.validate_result(task, result)


def test_partial_or_earlier_result_never_excluded(client, papers, monkeypatch):
    docs, value, body = papers
    async def partial(*args, **kwargs):
        result = await future_response(*args, **kwargs)
        result['assessments'][0]['relation'] = 'partial'; return result
    monkeypatch.setattr(model_client, 'chat_json', partial)
    row = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body).json()['assessments'][0]
    assert row['bucket'] == 'retain_for_review'
    record = literature_store.get_record(literature_store.imports(docs[1]['id'])[0]['key'])
    record.update(updated='2019-01-01T00:00:00Z'); literature_store.save_records([record])
    monkeypatch.setattr(model_client, 'chat_json', future_response)
    row = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body).json()['assessments'][0]
    assert row['temporal_status'] == 'not_later' and row['bucket'] == 'retain_for_review'


def test_source_deleted_during_generation_not_saved(client, papers, monkeypatch):
    docs, value, _ = papers
    async def fake(*args, **kwargs):
        proposed = await review_response(*args, **kwargs); store.delete_document(docs[0]['id']); return proposed
    monkeypatch.setattr(model_client, 'chat_json', fake)
    assert client.post(f"/api/graphs/{value['id']}/reviews", json={}).status_code == 409


@pytest.mark.parametrize('condition', ['same_day', 'wrong_version', 'wrong_hash'])
def test_unreliable_chronology_never_provisionally_excludes(client, papers, monkeypatch, condition):
    docs, value, body = papers
    provenance = literature_store.imports(docs[1]['id'])[0]
    if condition == 'same_day':
        record = literature_store.get_record(provenance['key']); record['updated'] = '2020-01-01T00:00:00Z'; literature_store.save_records([record])
    elif condition == 'wrong_version':
        store.update_document(docs[1]['id'], version_label='v2')
    else:
        literature_store.bind_import(docs[1], provenance['key'], {**provenance, 'sha256': 'incorrect-hash'})
    monkeypatch.setattr(model_client, 'chat_json', future_response)
    row = client.post(f"/api/graphs/{value['id']}/future-assessments", json=body).json()['assessments'][0]
    assert row['bucket'] == 'retain_for_review'
    assert row['temporal_status'] == ('same_day_uncertain' if condition == 'same_day' else 'unknown')


def test_reprocessing_keeps_old_review_bound_to_old_parse(client, papers, monkeypatch):
    docs, value, _ = papers
    monkeypatch.setattr(model_client, 'chat_json', review_response)
    response = client.post(f"/api/graphs/{value['id']}/reviews", json={}).json()
    store.start_reprocess(docs[0]['id'], {'ocr': False, 'tables': False})
    saved = client.get('/api/research-insights/' + response['id'])
    assert saved.status_code == 200
    assert saved.json()['sources'] == response['sources']


def test_pdf_quote_normalization_maps_back_to_exact_original():
    raw = 'This conﬁguration uses bidirec-\ntional\n attention.'
    result = insights.anchor_quote('This configuration uses bidirectional attention.', raw)
    assert result['quote'] == raw and result['match'] == 'normalized_pdf_text'
    assert raw[result['start']:result['end']] == result['quote']
    assert insights.anchor_quote('This configuration uses unidirectional attention.', raw) is None


def test_quote_normalization_does_not_fuzzy_match_or_change_numbers():
    assert insights.anchor_quote('Fictional 9-example result', 'Fictional 8-example result') is None
    assert insights.anchor_quote('normal model', 'normal\nmodel; normal\nmodel') is None
    assert insights.anchor_quote('bert encoder', 'BERT encoder') is None


def test_review_evidence_reserves_an_abstract_slot(monkeypatch):
    abstract = {'id':'abstract','text':'SYNTHETIC abstract ' * 8,'block_type':'paragraph','section_path':['Abstract']}
    topic = {'id':'topic','text':'SYNTHETIC method'}
    monkeypatch.setattr(qa := insights.qa, '_collect_sources', lambda _: [abstract])
    monkeypatch.setattr(qa, '_retrieve_question', lambda *args: [topic])
    monkeypatch.setattr(qa, '_retrieve', lambda *args: [])
    assert insights.review_evidence({'id':'fictional'}, 'fictional topic') == [abstract, topic]


def test_review_output_has_a_hard_total_claim_limit(client, papers, monkeypatch):
    _, value, _ = papers
    async def excessive(system, data, **kwargs):
        candidate = claim([data['evidence'][0]])
        return {'sections': [{'section': name, 'claims': [candidate] * 4} for name in insights.SECTIONS]}
    monkeypatch.setattr(model_client, 'chat_json', excessive)
    payload = client.post(f"/api/graphs/{value['id']}/reviews", json={}).json()
    assert sum(len(section['claims']) for section in payload['sections']) == 8
    assert payload['rejected_rows'] == 8


def test_workflow_review_recovery_and_bundle_reuse_saved_artifact(client, papers, monkeypatch):
    import io
    import zipfile
    from backend.app import workflow
    from test_workflow import start, wait
    docs, _, _ = papers
    calls = []
    async def fake(system, data, **kwargs):
        if kwargs['max_tokens'] == 2400:
            calls.append(1)
            return await review_response(system, data, **kwargs)
        return {'claims': []}
    monkeypatch.setattr(model_client, 'chat_json', fake)
    original = workflow.execute
    interrupted = False
    async def once(payload, step):
        nonlocal interrupted
        result = await original(payload, step)
        if step['tool'] == 'review' and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(workflow, 'execute', once)
    task = client.post('/api/research-tasks', json={'goal':'speech attention review', 'document_ids':[doc['id'] for doc in docs[:2]], 'actions':['review'], 'use_model':True}).json()
    start(client, task)
    paused = wait(client, task['id'])
    assert paused['status'] == 'paused'
    assert client.post('/api/research-tasks/'+task['id']+'/control', json={'action':'resume'}).status_code == 200
    finished = wait(client, task['id'])
    assert finished['status'] == 'completed_with_gaps', finished
    result = next(step['result'] for step in finished['steps'] if step['tool'] == 'review')
    assert result['mode'] == 'llm' and result['sections'] and len(calls) == 1
    with zipfile.ZipFile(io.BytesIO(client.get('/api/research-tasks/'+task['id']+'/export').content)) as archive:
        assert 'review.md' in archive.namelist()
        assert 'ParseRun:' in archive.read('review.md').decode()
