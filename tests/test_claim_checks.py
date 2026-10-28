"""Injected responses test software guards, not scientific model quality."""
import asyncio
import copy
import json
import pytest
from test_insights import papers, review_response
from backend.app import claim_checks, insights, model_client, store


@pytest.fixture
def draft(client, papers, monkeypatch):
    _, graph, _ = papers
    monkeypatch.setattr(model_client, 'chat_json', review_response)
    return client.post(f"/api/graphs/{graph['id']}/reviews", json={}).json()


def supported(data):
    return {'checks': [{'claim_id': row['claim_id'], 'status': 'supported', 'reason': 'SYNTHETIC: exact quotation supports this fixture.', 'unsupported_parts': [], 'quote_support': row['quotes']} for row in data['claims']]}


def test_only_attached_quotes_are_sent_and_original_is_immutable(client, draft, monkeypatch):
    calls = []
    async def fake(system, data, **kwargs):
        calls.append(data)
        assert kwargs['max_tokens'] == 2400
        assert all(set(row) == {'claim_id','text','kind','quotes'} for row in data['claims'])
        assert all(set(quote) == {'evidence_id','quote'} for row in data['claims'] for quote in row['quotes'])
        return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    response = client.post(f"/api/research-insights/{draft['id']}/checks", json={})
    assert response.status_code == 200, response.text
    check = response.json()
    assert check['counts']['supported'] == 3 and check['semantic_verified'] is False
    assert client.get('/api/research-insights/'+draft['id']).json() == draft
    assert len(client.get(f"/api/research-insights/{draft['id']}/checks").json()['items']) == 1
    assert client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()['id'] == check['id'] and len(calls) == 1
    exported = client.get('/api/insight-checks/'+check['id']+'/export')
    assert exported.status_code == 200 and 'ParseRun:' in exported.text and 'SHA256' in exported.text
    assert client.get('/api/research-insights/'+check['id']+'/export').status_code == 200


def test_parallel_identical_checks_coalesce_in_single_worker(client, draft, monkeypatch):
    calls=[]
    async def fake(system, data, **kwargs):
        calls.append(1); await asyncio.sleep(.03); return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    async def run(): return await asyncio.gather(claim_checks.create(draft['id']), claim_checks.create(draft['id']))
    values = asyncio.run(run())
    assert values[0]['id'] == values[1]['id'] and len(calls) == 1


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'bad_status', 'unknown_evidence', 'fabricated_quote', 'invalid_parts', 'empty_reason', 'unhashable_status', 'missing_support', 'cross_doc_omission'])
def test_malformed_or_unanchored_output_is_never_supported(client, draft, monkeypatch, mutation):
    async def fake(system, data, **kwargs):
        value=supported(data); row=value['checks'][-1]
        if mutation == 'duplicate': value['checks'].append(copy.deepcopy(row))
        elif mutation == 'missing': value['checks'].pop()
        elif mutation == 'bad_status': row['status']='definitely_true'
        elif mutation == 'unknown_evidence': row['quote_support'][0]['evidence_id']='unknown'
        elif mutation == 'fabricated_quote': row['quote_support'][0]['quote']='fabricated content'
        elif mutation == 'invalid_parts': row['unsupported_parts']=['not in original claim']
        elif mutation == 'empty_reason': row['reason']=' '
        elif mutation == 'unhashable_status': row['status']=[]
        elif mutation == 'missing_support': row['quote_support']=[]
        elif mutation == 'cross_doc_omission': row['quote_support']=row['quote_support'][:1]
        return value
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    assert result['checks'][-1]['status'] == 'insufficient' and result['checks'][-1]['output_valid'] is False


def test_supported_with_unsupported_clause_is_downgraded_and_export_retains_original(client, draft, monkeypatch):
    async def fake(system, data, **kwargs):
        result=supported(data)
        result['checks'][0]['unsupported_parts']=[data['claims'][0]['text'][-12:]]
        return result
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    assert result['checks'][0]['status'] == 'partial'
    text=client.get('/api/insight-checks/'+result['id']+'/export').text
    assert draft['sections'][0]['claims'][0]['text'] in text.split('待修订原陈述')[1]
    assert '缺乏支持的片段' in text


def test_empty_or_wrong_scope_cannot_start_a_check(client, draft, monkeypatch):
    async def fake(system, data, **kwargs): return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    result=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    assert client.post(f"/api/research-insights/{result['id']}/checks", json={}).status_code == 422
    empty=copy.deepcopy(draft); empty['id']='synthetic-empty-draft'; empty['sections']=[]; insights.persist(empty)
    assert client.post(f"/api/research-insights/{empty['id']}/checks", json={}).status_code == 422


@pytest.mark.parametrize('failure,expected', [('budget',429),('provider',502),('not_configured',503)])
def test_failures_never_save_fake_verification(client, draft, monkeypatch, failure, expected):
    async def fake(*args, **kwargs): raise ValueError('model_budget_exhausted' if failure=='budget' else 'invalid_model_output')
    monkeypatch.setattr(model_client, 'chat_json', fake)
    if failure=='not_configured': monkeypatch.setenv('LLM_API_KEY','')
    assert client.post(f"/api/research-insights/{draft['id']}/checks", json={}).status_code == expected
    assert not client.get(f"/api/research-insights/{draft['id']}/checks").json()['items']


def test_changed_model_makes_new_check_while_old_history_is_retained(client, draft, monkeypatch):
    async def fake(system, data, **kwargs): return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    first=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    monkeypatch.setenv('LLM_MODEL','synthetic-second-model')
    second=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    assert first['id'] != second['id'] and len(client.get(f"/api/research-insights/{draft['id']}/checks").json()['items']) == 2


def test_source_deletion_during_check_or_after_save_invalidates_records(client, draft, monkeypatch):
    async def fake(system, data, **kwargs):
        store.delete_document(draft['sources'][0]['document_id']); return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    assert client.post(f"/api/research-insights/{draft['id']}/checks", json={}).status_code in {404,409}
    with store.connection() as conn:
        assert not conn.execute("SELECT 1 FROM research_insights WHERE kind='check' AND json_extract(payload,'$.parent_insight_id')=?",(draft['id'],)).fetchone()


def test_saved_check_is_purged_when_source_deleted(client, draft, monkeypatch):
    async def fake(system, data, **kwargs): return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    saved=client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()
    assert client.delete('/api/documents/'+draft['sources'][0]['document_id']).status_code == 200
    assert client.get('/api/insight-checks/'+saved['id']+'/export').status_code == 404
    with store.connection() as conn: assert not conn.execute('SELECT 1 FROM research_insights WHERE id=?',(saved['id'],)).fetchone()


@pytest.mark.parametrize('status', ['partial', 'contradicted', 'insufficient'])
def test_valid_non_support_verdicts_remain_withheld(status):
    claim={'claim_id':'c1','text':'SYNTHETIC model always works.', 'quotes':[{'evidence_id':'e1','quote':'SYNTHETIC model sometimes fails.'}]}
    row={'status':status,'reason':'SYNTHETIC bounded reason','unsupported_parts':['always works'],'quote_support':[{'evidence_id':'e1','quote':'sometimes fails'}]}
    result=claim_checks.checked_row(row,claim)
    assert result['status'] == status and result['output_valid'] is True


def test_future_claim_is_checked_without_promoting_its_original_relation(client, papers, monkeypatch):
    from test_insights import future_response
    _, graph, body = papers
    monkeypatch.setattr(model_client,'chat_json',future_response)
    parent=client.post(f"/api/graphs/{graph['id']}/future-assessments",json=body).json()
    async def fake(system,data,**kwargs):
        result=supported(data);result['checks'][0]['status']='partial';result['checks'][0]['unsupported_parts']=[data['claims'][0]['text'][-10:]];return result
    monkeypatch.setattr(model_client,'chat_json',fake)
    checked=client.post(f"/api/research-insights/{parent['id']}/checks",json={}).json()
    assert checked['counts']['partial'] == 1
    assert client.get('/api/research-insights/'+parent['id']).json() == parent


def test_single_paper_claim_cannot_be_presented_as_cross_paper_comparison(client, papers, monkeypatch):
    from test_insights import claim
    _, graph, _=papers
    async def fake(system,data,**kwargs):
        return {'sections':[{'section':'comparison','claims':[claim(data['evidence'][:1])]}]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    value=client.post(f"/api/graphs/{graph['id']}/reviews",json={}).json()
    assert value['sections'] == [] and value['rejected_rows'] == 1
