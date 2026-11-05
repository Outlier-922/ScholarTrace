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
    return {'checks': [{'claim_id': row['claim_id'], 'status': 'supported', 'reason': 'SYNTHETIC: exact quotation supports this fixture.', 'unsupported_parts': [], 'quote_support': row['quotes'],
        'scope': {'status': 'aligned', 'claim_excerpt': row['text'], 'reason': 'SYNTHETIC: scope matches the fixture.'}} for row in data['claims']]}


def test_only_attached_quotes_are_sent_and_original_is_immutable(client, draft, monkeypatch):
    calls = []
    async def fake(system, data, **kwargs):
        calls.append(data)
        assert kwargs['max_tokens'] == 2400
        assert all(set(row) == {'claim_id','text','kind','quotes'} for row in data['claims'])
        assert all(set(quote) == {'evidence_id','quote','source_id'} for row in data['claims'] for quote in row['quotes'])
        by_id = {e['id']: e['document_id'] for e in draft['evidence']}
        pairs = [(by_id[q['evidence_id']], q['source_id']) for row in data['claims'] for q in row['quotes']]
        assert all((left[0] == right[0]) == (left[1] == right[1]) for left in pairs for right in pairs)
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
    row['scope']={'status':'aligned','claim_excerpt':claim['text'],'reason':'SYNTHETIC scope.'}
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


@pytest.mark.parametrize('scope_status,expected', [('broader', 'partial'), ('unclear', 'insufficient'), ('aligned', 'supported')])
def test_scope_verdict_controls_support_even_when_main_verdict_says_supported(scope_status, expected):
    claim={'claim_id':'c1','text':'SYNTHETIC method always uses loss A; the authors used loss A.',
           'quotes':[{'evidence_id':'e1','quote':'SYNTHETIC one reported experiment uses loss A.'}]}
    row=supported({'claims':[claim]})['checks'][0]
    row['scope']={'status':scope_status,'claim_excerpt':'method always uses loss A','reason':'SYNTHETIC reported setting compared with a general method statement.'}
    result=claim_checks.checked_row(row,claim)
    assert result['status']==expected and result['output_valid'] is True
    if scope_status=='broader': assert 'method always uses loss A' in result['unsupported_parts']


@pytest.mark.parametrize('mutation', ['missing', 'invalid_status', 'unhashable_status', 'fabricated_excerpt', 'blank_excerpt', 'empty_reason'])
def test_missing_or_malformed_scope_cannot_produce_support(mutation):
    claim={'claim_id':'c1','text':'SYNTHETIC bounded statement.', 'quotes':[{'evidence_id':'e1','quote':'SYNTHETIC bounded quotation.'}]}
    row=supported({'claims':[claim]})['checks'][0]
    if mutation=='missing': row.pop('scope')
    elif mutation=='invalid_status': row['scope']['status']='certain'
    elif mutation=='unhashable_status': row['scope']['status']=[]
    elif mutation=='fabricated_excerpt': row['scope']['claim_excerpt']='invented assertion'
    elif mutation=='blank_excerpt': row['scope']['claim_excerpt']=' '
    elif mutation=='empty_reason': row['scope']['reason']=' '
    result=claim_checks.checked_row(row,claim)
    assert result['status']=='insufficient' and result['output_valid'] is False


def test_new_prompt_does_not_reuse_old_scope_free_check_and_old_export_survives(client,draft,monkeypatch):
    calls=[]
    async def fake(system,data,**kwargs): calls.append(1);return supported(data)
    monkeypatch.setattr(model_client,'chat_json',fake)
    monkeypatch.setattr(claim_checks,'PROMPT_VERSION','attached-quotes-v1')
    old=client.post(f"/api/research-insights/{draft['id']}/checks",json={}).json()
    # A persisted legacy record lacks scope. Keep it readable without rewriting it.
    old['id']='synthetic-legacy-scope-free'
    for row in old['checks']: row.pop('scope')
    insights.persist(old)
    assert client.get('/api/insight-checks/'+old['id']+'/export').status_code==200
    monkeypatch.setattr(claim_checks,'PROMPT_VERSION','attached-quotes-scope-v2')
    new=client.post(f"/api/research-insights/{draft['id']}/checks",json={}).json()
    assert new['id']!=old['id'] and len(calls)==2
    assert '范围对应（模型建议）' in client.get('/api/insight-checks/'+new['id']+'/export').text
    assert client.get('/api/research-insights/'+old['id']).json()==old


@pytest.mark.parametrize('text,quote,expected', [
    ('SYNTHETIC method uses loss A.', 'Our model uses loss A.', 'insufficient'),
    ('SYNTHETIC authors report their model uses loss A.', 'Our model uses loss A.', 'insufficient'),
    ('SYNTHETIC method uses loss A.', 'We perform finetuning with loss A.', 'insufficient'),
    ('SYNTHETIC results are conditional.', 'Our\nexperiments use a restricted setting.', 'insufficient'),
    ('SYNTHETIC result.', '我们的模型在该设置中使用损失函数。', 'insufficient'),
    ('SYNTHETIC method uses loss A.', 'Our method defines the loss A objective.', 'supported'),
    ('SYNTHETIC method uses loss A.', 'The method uses loss A.', 'supported'),
])
def test_literal_setting_cautions_withhold_even_scoped_claims_without_claiming_semantic_error(text,quote,expected):
    claim={'claim_id':'c1','text':text,'quotes':[{'evidence_id':'e1','quote':quote}]}
    row=supported({'claims':[claim]})['checks'][0]
    result=claim_checks.checked_row(row,claim)
    assert result['status']==expected and result['output_valid'] is True
    assert result['scope']['status']=='aligned'  # Retain the actual model suggestion.
    if expected=='insufficient':
        assert result['scope_cautions'][0]['cue'] in quote
        assert result['unsupported_parts']==[]  # This guard does not invent an unsupported clause.
    else: assert result['scope_cautions']==[]


def test_changed_local_validation_does_not_reuse_previous_check(client,draft,monkeypatch):
    calls=[]
    async def fake(system,data,**kwargs): calls.append(1);return supported(data)
    monkeypatch.setattr(model_client,'chat_json',fake)
    first=client.post(f"/api/research-insights/{draft['id']}/checks",json={}).json()
    monkeypatch.setattr(claim_checks,'VALIDATION_VERSION','synthetic-next-validation')
    second=client.post(f"/api/research-insights/{draft['id']}/checks",json={}).json()
    assert first['id']!=second['id'] and len(calls)==2
