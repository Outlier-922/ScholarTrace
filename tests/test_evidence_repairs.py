"""Synthetic software tests, not evidence of scientific revision accuracy."""
import asyncio
import copy
import pytest
from test_claim_checks import draft, supported
from test_insights import papers
from backend.app import claim_checks, evidence_repairs as repairs, insights, model_client, store


@pytest.fixture
def checked(client, draft, monkeypatch):
    async def fake(system, data, **kwargs): return supported(data)
    monkeypatch.setattr(model_client, 'chat_json', fake)
    return client.post(f"/api/research-insights/{draft['id']}/checks", json={}).json()


def prepare(client, checked, count=3):
    response=client.post(f"/api/insight-checks/{checked['id']}/repair-plans", json={'claim_ids':[c['claim_id'] for c in checked['claims'][:count]]})
    assert response.status_code == 200, response.text
    return response.json()


async def revision_response(system, data, **kwargs):
    assert kwargs['max_tokens'] == 2400
    evidence={e['id']:e for e in data['evidence']}
    rows=[]
    for claim in data['claims']:
        by_doc={}
        for identity in claim['evidence_ids']:
            item=evidence[identity]
            by_doc.setdefault(item['document_id'],item)
        rows.append({'claim_id':claim['claim_id'],'action':'revise','reason':'SYNTHETIC narrowing only.', 'claim':{'kind':claim['kind'],'text':'SYNTHETIC revision remains unverified.', 'evidence_ids':[e['id'] for e in by_doc.values()], 'quotes':[{'evidence_id':e['id'],'quote':e['text'][:140]} for e in by_doc.values()]}})
    return {'revisions':rows}


def test_preview_is_local_scoped_cached_and_exportable(client, checked, monkeypatch):
    async def forbidden(*args,**kwargs): raise AssertionError('No provider for preview')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    plan=prepare(client,checked)
    assert len(plan['queries']) <= 12 and len(plan['evidence']) <= 24
    assert plan['id'] == prepare(client,checked)['id']
    originals={e['id']:e for e in checked['evidence']}
    for scope in plan['scopes']:
        allowed={originals[q['evidence_id']]['document_id'] for q in scope['original']['quotes']}
        assert set(scope['document_ids'])==allowed
        assert all(e['document_id'] in allowed for e in plan['evidence'] if e['id'] in scope['evidence_ids'])
    assert client.get(f"/api/insight-checks/{checked['id']}/repair-plans").json()['items'][0]['id']==plan['id']
    assert 'ParseRun:' in client.get(f"/api/repair-plans/{plan['id']}/export").text
    assert client.get(f"/api/research-insights/{plan['id']}/export").status_code==200


def test_revision_is_separate_recheckable_and_preserves_original(client, checked, monkeypatch):
    plan=prepare(client,checked);calls=[]
    async def fake(*args,**kwargs): calls.append(1);return await revision_response(*args,**kwargs)
    monkeypatch.setattr(model_client,'chat_json',fake)
    result=client.post(f"/api/repair-plans/{plan['id']}/revise",json={})
    assert result.status_code==200,result.text
    value=result.json()
    assert len(value['repair']['items'])==3 and all(row['revised'] for row in value['repair']['items'])
    assert value['semantic_verified'] is False and value['kind']=='review'
    assert client.get('/api/research-insights/'+checked['id']).json()==checked
    assert client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).json()['id']==value['id'] and len(calls)==1
    text=client.get('/api/research-insights/'+value['id']+'/export').text
    assert '修订轨迹' in text and '原句：' in text and 'ParseRun:' in text
    async def support(system,data,**kwargs):return supported(data)
    monkeypatch.setattr(model_client,'chat_json',support)
    assert client.post('/api/research-insights/'+value['id']+'/checks',json={}).status_code==200


@pytest.mark.parametrize('mutation',['unknown_evidence','novel_number','fake_quote','wrong_kind','duplicate','missing','unresolved','unhashable_action'])
def test_invalid_revision_never_silently_reuses_original(client,checked,monkeypatch,mutation):
    plan=prepare(client,checked,1)
    async def fake(*args,**kwargs):
        value=await revision_response(*args,**kwargs);row=value['revisions'][0]
        if mutation=='unknown_evidence':row['claim']['evidence_ids']=['foreign-evidence']
        elif mutation=='novel_number':row['claim']['text']='SYNTHETIC 99999999 examples.'
        elif mutation=='fake_quote':row['claim']['quotes'][0]['quote']='fabricated sentence'
        elif mutation=='wrong_kind':row['claim']['kind']='synthesis'
        elif mutation=='duplicate':value['revisions'].append(copy.deepcopy(row))
        elif mutation=='missing':value['revisions']=[]
        elif mutation=='unresolved':row['action']='unresolved'
        elif mutation=='unhashable_action':row['action']=[]
        return value
    monkeypatch.setattr(model_client,'chat_json',fake)
    value=client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).json()
    assert value['sections']==[] and value['repair']['items'][0]['status']=='unresolved'
    assert value['repair']['items'][0]['original']['text']==checked['claims'][0]['text']


def test_evidence_from_another_selected_claim_is_not_allowed(client,checked,monkeypatch):
    plan=prepare(client,checked,2)
    async def fake(*args,**kwargs):
        result=await revision_response(*args,**kwargs)
        result['revisions'][0]['claim']=copy.deepcopy(result['revisions'][1]['claim'])
        return result
    monkeypatch.setattr(model_client,'chat_json',fake)
    value=client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).json()
    assert value['repair']['items'][0]['revised'] is None
    assert value['repair']['items'][1]['revised']


@pytest.mark.parametrize('ids',[[],['foreign'],['s0c0']*4])
def test_invalid_selection_is_rejected(client,checked,ids):
    assert client.post(f"/api/insight-checks/{checked['id']}/repair-plans",json={'claim_ids':ids}).status_code==422


def test_old_parse_is_pinned_after_reprocessing(client,checked):
    original=checked['sources'][0]
    store.start_reprocess(original['document_id'],{'ocr':False,'tables':False})
    plan=prepare(client,checked,1)
    assert all(q['parse_run_id']==original['parse_run_id'] for q in plan['queries'])
    assert plan['evidence']


def test_context_windows_and_adjacent_blocks_remain_separate(monkeypatch):
    def item(identity,text,order): return {'id':identity,'document_id':'d1','parse_run_id':'p1','block_id':identity,'title':'SYNTHETIC','text':text,'source_order':order,'page':1,'block_type':'paragraph','metadata':{}}
    a=item('a','SYNTHETIC preceding context.',0);b=item('b','SYNTHETIC target text.',1)
    monkeypatch.setattr(store,'get_document',lambda _: {'id':'d1','title':'SYNTHETIC'})
    monkeypatch.setattr(repairs.qa,'_collect_sources',lambda _: [a,b])
    monkeypatch.setattr(repairs.qa,'_retrieve',lambda *args: [])
    claim={'claim_id':'c1','text':'SYNTHETIC statement','quotes':[{'evidence_id':'old-b','quote':'target text'}]}
    check={'sources':[{'document_id':'d1','parse_run_id':'p1'}],'evidence':[{**b,'id':'old-b'}]}
    evidence,_,_=repairs.collect(check,[claim])
    assert {e['text'] for e in evidence}=={a['text'],b['text']}
    assert all(e['model_excerpt']==e['text'] for e in evidence)
    long=item('long','prefix '*400+'SYNTHETIC ending quote.',2)
    focused=repairs.window(long,'SYNTHETIC ending quote.')
    assert 'SYNTHETIC ending quote.' in focused['model_excerpt'] and len(focused['model_excerpt'])<=1600


@pytest.mark.parametrize('failure,status',[('budget',429),('provider',502),('configuration',503)])
def test_failed_generation_keeps_preview_without_fake_revision(client,checked,monkeypatch,failure,status):
    plan=prepare(client,checked,1)
    async def fake(*args,**kwargs):raise ValueError('model_budget_exhausted' if failure=='budget' else 'invalid_model_output')
    monkeypatch.setattr(model_client,'chat_json',fake)
    if failure=='configuration':monkeypatch.setenv('LLM_API_KEY','')
    assert client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).status_code==status
    assert client.get(f"/api/repair-plans/{plan['id']}").status_code==200
    assert not repairs.history_rows(checked['id'],'review')


def test_source_deleted_during_rewrite_is_not_saved(client,checked,monkeypatch):
    plan=prepare(client,checked,1)
    async def fake(*args,**kwargs):
        proposed=await revision_response(*args,**kwargs)
        store.delete_document(checked['sources'][0]['document_id']);return proposed
    monkeypatch.setattr(model_client,'chat_json',fake)
    assert client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).status_code in {404,409}


def test_future_reason_revision_does_not_promote_resolution(client,papers,monkeypatch):
    from test_insights import future_response
    _,graph,body=papers
    monkeypatch.setattr(model_client,'chat_json',future_response)
    parent=client.post(f"/api/graphs/{graph['id']}/future-assessments",json=body).json()
    async def fake(system,data,**kwargs):return supported(data)
    monkeypatch.setattr(model_client,'chat_json',fake)
    check=client.post(f"/api/research-insights/{parent['id']}/checks",json={}).json()
    plan=prepare(client,check,1)
    monkeypatch.setattr(model_client,'chat_json',revision_response)
    value=client.post(f"/api/repair-plans/{plan['id']}/revise",json={}).json()
    assert value['kind']=='future' and value['assessments'][0]['bucket']=='retain_for_review'
    assert value['assessments'][0]['relation']=='insufficient' and value['assessments'][0]['claim']
    assert client.get('/api/research-insights/'+parent['id']).json()==parent


def test_same_block_quotes_use_a_real_continuous_span_without_mutating_proposal():
    evidence={'id':'e1','document_id':'d1','parse_run_id':'p1','block_id':'b1','text':'SYNTHETIC attention uses parallel computation. Results remain conditional.'}
    original={'kind':'paper_fact','text':'SYNTHETIC conditional result.','evidence_ids':['e1'],'quotes':[{'evidence_id':'e1','quote':'SYNTHETIC attention'},{'evidence_id':'e1','quote':'Results remain conditional'}]}
    before=copy.deepcopy(original)
    canonical,notes=repairs.canonical_claim(original,[evidence])
    assert original==before and len(canonical['quotes'])==1 and notes
    assert canonical['quotes'][0]['quote'] in evidence['text']
    assert insights.checked_claim(canonical,[evidence])
    evidence['text']='SYNTHETIC attention '+'x'*400+' Results remain conditional.'
    canonical,notes=repairs.canonical_claim(original,[evidence])
    assert insights.checked_claim(canonical,[evidence]) is None and not notes


def test_cross_page_word_fragment_is_separate_and_ambiguous_links_are_not_auto_added():
    seed={'id':'seed','document_id':'d1','parse_run_id':'p1','block_id':'b1','page':4,'text':'former is called a Transformer encoder.'}
    before={'id':'before','document_id':'d1','parse_run_id':'p1','block_id':'b0','page':3,'text':'SYNTHETIC the bidirectional Trans-'}
    original={'kind':'paper_fact','text':'SYNTHETIC Transformer encoder.','evidence_ids':['seed'],'quotes':[{'evidence_id':'seed','quote':seed['text']}]}
    fragments=repairs.page_fragments(seed,[before],original)
    assert len(fragments)==1 and fragments[0]['text']==before['text']
    canonical,notes=repairs.canonical_claim(original,[seed,*fragments])
    assert len(canonical['quotes'])==2 and notes
    assert all(q['quote'] in next(e['text'] for e in [seed,*fragments] if e['id']==q['evidence_id']) for q in canonical['quotes'])
    ambiguous={**fragments[0],'id':'ambiguous','text':'SYNTHETIC another Trans-','model_excerpt':'SYNTHETIC another Trans-'}
    canonical,notes=repairs.canonical_claim(original,[seed,*fragments,ambiguous])
    assert len(canonical['quotes'])==1 and not notes
    assert not repairs.page_fragments(seed,[{**before,'page':2}],original)


def test_saved_response_can_be_revalidated_against_new_preview_without_model(client,checked,monkeypatch):
    old=prepare(client,checked,1)
    monkeypatch.setattr(model_client,'chat_json',revision_response)
    first=client.post(f"/api/repair-plans/{old['id']}/revise",json={}).json()
    monkeypatch.setattr(repairs,'RETRIEVAL_VERSION','SYNTHETIC-next-retrieval')
    new=prepare(client,checked,1)
    async def forbidden(*args,**kwargs):raise AssertionError('Replay cannot reach provider')
    monkeypatch.setattr(model_client,'chat_json',forbidden);monkeypatch.setenv('LLM_API_KEY','')
    response=client.post(f"/api/repair-plans/{new['id']}/revise",json={'reuse_revision_id':first['id']})
    assert response.status_code==200,response.text
    value=response.json()
    assert value['id']!=first['id'] and value['repair']['reused_response_from']==first['id']
    assert value['model_proposal']==first['model_proposal']
    assert client.post(f"/api/repair-plans/{new['id']}/revise",json={'reuse_revision_id':value['id']}).json()['id']==value['id']
    assert client.get(f"/api/repair-plans/{new['id']}/revisions").json()['items'][0]['id']==value['id']


def test_replay_cannot_change_which_claims_were_selected(client,checked,monkeypatch):
    old=prepare(client,checked,1)
    monkeypatch.setattr(model_client,'chat_json',revision_response)
    first=client.post(f"/api/repair-plans/{old['id']}/revise",json={}).json()
    new=prepare(client,checked,2)
    assert client.post(f"/api/repair-plans/{new['id']}/revise",json={'reuse_revision_id':first['id']}).status_code==422
