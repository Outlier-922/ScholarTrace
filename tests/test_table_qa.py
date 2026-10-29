"""Opt-in source-cell QA and strict separation of unverified manual drafts."""
import json
import pytest
from conftest import upload_pdf,wait_for_document
from test_tables import grid_pdf
from test_table_refinement import region
from backend.app import qa,table_qa,table_refine_store as repo,store,model_client


def body(region,**kwargs):
    doc,_,source=region
    return {'question':'Synthetic Alpha Accuracy','document_ids':[doc['id']],'table_sources':[{'source_id':source['id']}],'table_only':True,'use_model':False,**kwargs}


def revision(client,source,number='987654321.123'):
    rows=[r[:] for r in source['grid']['rows']];rows[0][1]=number
    response=client.post('/api/table-regions/'+source['id']+'/revisions',json={'rows':rows,'labels':[c['label'] for c in source['grid']['columns']],'note':'SYNTHETIC TEST ONLY. PROMPT_INJECTION_SENTINEL. Not an experiment.'})
    assert response.status_code==200,response.text
    return response.json()


def configured(monkeypatch):
    for k,v in {'LLM_API_KEY':'synthetic-key','LLM_BASE_URL':'https://synthetic.example/v1','LLM_MODEL':'synthetic'}.items():monkeypatch.setenv(k,v)


def test_opt_in_native_cells_retrieve_exact_scope_without_dense(client,region,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Table-only must not query the global dense/text index')
    monkeypatch.setattr(qa,'_retrieve_question',forbidden)
    r=client.post('/api/ask',json=body(region));assert r.status_code==200,r.text
    result=r.json();assert result['retrieval']['mode']=='table_bm25' and result['evidence']
    assert all(v['source_kind']=='table_cell' and table_qa.valid_evidence(v) for v in result['evidence'])
    assert result['evidence'][0]['table_cell']['value']=='72.5'
    assert result['table_context'][0]['parse_run_id']==region[0]['parse_run_id']


def test_mixed_retrieval_keeps_prose_and_table_lanes(client,region):
    r=client.post('/api/ask',json=body(region,table_only=False)).json()
    assert r['retrieval']['mode']=='mixed_tables'
    assert any(v.get('source_kind')=='table_cell' for v in r['evidence'])
    assert any(v.get('source_kind') is None for v in r['evidence'])


def test_manual_values_and_notes_never_reach_model(client,region,monkeypatch):
    configured(monkeypatch);source=region[2];r=revision(client,source);calls=[]
    async def fake(system,payload,**kwargs):
        serialized=json.dumps(payload)
        assert '987654321.123' not in serialized and 'PROMPT_INJECTION_SENTINEL' not in serialized+system
        assert 'table_drafts' not in payload
        e=next(v for v in payload['evidence'] if '单元格值：72.5' in v['text']);calls.append(1)
        return {'claims':[{'text':'A wrong narrative label with the actual value 72.5','evidence_ids':[e['id']]}],'missing_information':[]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':source['id'],'revision_id':r['id']}],use_model=True)).json()
    assert len(calls)==1 and result['claims'][0]['status']=='table_candidate'
    assert 'wrong narrative' not in result['answer'] and '987654321.123' not in result['answer']
    assert any('987654321.123' in v['text'] for v in result['table_drafts'])
    assert all(v['table_draft']['sent_to_model'] is False for v in result['table_drafts'])


def test_model_cannot_turn_manual_value_into_paper_fact(client,region,monkeypatch):
    configured(monkeypatch);source=region[2];r=revision(client,source)
    async def fake(system,payload,**kwargs):return {'claims':[{'text':'Accuracy is 987654321.123','evidence_ids':[payload['evidence'][0]['id']]}]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':source['id'],'revision_id':r['id']}],use_model=True)).json()
    assert result['claims']==[] and result['table_drafts']


def test_manual_only_selected_row_skips_model(client,region,monkeypatch):
    configured(monkeypatch);doc,request,_=region
    response=client.post('/api/table-regions',json={**request,'column_cuts':[.15]});assert response.status_code==200,response.text
    source=response.json();assert source['grid']['rows'][0]==[None,None]
    rows=[r[:] for r in source['grid']['rows']];rows[0]=['SYNTHETIC manual Alpha','987654321.123']
    r=client.post('/api/table-regions/'+source['id']+'/revisions',json={'rows':rows,'labels':['Name','Metric'],'note':'SYNTHETIC hand entry only'}).json()
    async def forbidden(*args,**kwargs):raise AssertionError('No native cell facts, no provider request')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':source['id'],'revision_id':r['id'],'row_indices':[0]}],question='SYNTHETIC manual Alpha',use_model=True)).json()
    assert not result['evidence'] and not result['claims'] and result['table_drafts']
    assert result['mode']=='evidence_only'


@pytest.mark.parametrize('modification,status',[
    ({'table_sources':[{'source_id':'00000000-0000-0000-0000-000000000000'}]},404),
    ({'table_sources':[]},422),
    ({'row_indices':[999]},422),({'row_indices':[True]},422),({'row_indices':[]},422),
    ({'row_indices':[0,0]},422),({'row_indices':list(range(13))},422),
    ({'revision_id':'00000000-0000-0000-0000-000000000000'},422),
])
def test_unavailable_or_invalid_table_scope_rejected(client,region,modification,status):
    b=body(region)
    if 'table_sources' in modification:b.update(modification)
    else:b['table_sources'][0].update(modification)
    assert client.post('/api/ask',json=b).status_code==status


def test_duplicate_and_excess_sources_rejected(client,region):
    ref=body(region)['table_sources'][0]
    assert client.post('/api/ask',json=body(region,table_sources=[ref,ref])).status_code==422
    assert client.post('/api/ask',json=body(region,table_sources=[ref]*7)).status_code==422


def test_wrong_document_and_foreign_revision_rejected(client,region,sample_pdfs):
    other=wait_for_document(client,upload_pdf(client,sample_pdfs['distinct'])['id'])
    try:assert client.post('/api/ask',json=body(region,document_ids=[other['id']])).status_code==422
    finally:client.delete('/api/documents/'+other['id'])
    assert client.post('/api/ask',json=body(region,table_sources=[{'source_id':region[2]['id'],'revision_id':region[2]['id']}])).status_code==422


def test_exact_historical_parse_is_pinned_and_conflicts_rejected(client,region):
    doc,_,s=region;old=doc['parse_run_id']
    response=client.post('/api/documents/'+doc['id']+'/reprocess',json={'ocr':False,'tables':False});assert response.status_code==200,response.text
    latest=wait_for_document(client,doc['id']);assert latest['parse_run_id']!=old
    result=client.post('/api/ask',json=body(region)).json()
    assert result['evidence'] and all(e['parse_run_id']==old for e in result['evidence'])
    assert client.post('/api/ask',json=body(region,parse_runs={doc['id']:latest['parse_run_id']})).status_code==422


def test_row_filter_does_not_leak_other_rows(client,region):
    s=region[2]
    result=client.post('/api/ask',json=body(region,question='Synthetic metric accuracy',table_sources=[{'source_id':s['id'],'row_indices':[1]}])).json()
    assert result['evidence'] and {e['table_cell']['row'] for e in result['evidence']}=={1}


def test_new_revision_does_not_rewrite_saved_answer_context(client,region):
    s=region[2];old=revision(client,s)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':s['id'],'revision_id':old['id']}])).json()
    revision(client,s,'876543210.321')
    historical=client.get('/api/agent-runs/'+result['run_id']).json()
    assert historical['table_drafts'] and all(d['table_draft']['revision_id']==old['id'] for d in historical['table_drafts'])
    assert '876543210.321' not in json.dumps(historical)


def test_historical_run_strips_unavailable_table_and_manual_text(client,region):
    s=region[2];r=revision(client,s)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':s['id'],'revision_id':r['id']}])).json()
    repo.purge_document(region[0]['id'])
    historical=client.get('/api/agent-runs/'+result['run_id']).json()
    assert not historical['evidence'] and not historical['table_drafts']
    assert historical['table_context'][0]['available'] is False
    assert '987654321.123' not in json.dumps(historical)


def test_source_purged_during_model_await_drops_claim(client,region,monkeypatch):
    configured(monkeypatch)
    async def fake(system,payload,**kwargs):
        e=next(e for e in payload['evidence'] if '单元格值：72.5' in e['text'])
        repo.purge_document(region[0]['id'])
        return {'claims':[{'text':'Accuracy72.5','evidence_ids':[e['id']]}]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    result=client.post('/api/ask',json=body(region,use_model=True)).json()
    assert result['claims']==[] and result['evidence']==[] and result['table_context'][0]['available'] is False


@pytest.mark.parametrize('tamper',['bbox','row','value'])
def test_forged_cell_metadata_is_invalid(client,region,tamper):
    evidence=client.post('/api/ask',json=body(region)).json()['evidence'][0]
    assert table_qa.valid_evidence(evidence)
    if tamper=='bbox':evidence['bbox']=[0,0,1,1]
    else:evidence['table_cell'][tamper]=999
    assert not table_qa.valid_evidence(evidence)


def test_forged_draft_note_is_invalid(client,region):
    r=revision(client,region[2])
    draft=client.post('/api/ask',json=body(region,table_sources=[{'source_id':region[2]['id'],'revision_id':r['id']}])).json()['table_drafts'][0]
    assert table_qa.valid_evidence(draft)
    draft['table_draft']['note']='forged'
    assert not table_qa.valid_evidence(draft)


def test_table_claims_are_compiled_from_one_cell_not_model_relabeling(client,region):
    evidence=client.post('/api/ask',json=body(region)).json()['evidence'];a=evidence[0]
    result=qa._validate_claims({'claims':[{'text':'Invented laboratory got '+a['table_cell']['value'],'evidence_ids':[a['id']]}]},evidence)
    assert result[0]['text']==a['text'] and 'Invented laboratory' not in result[0]['text']
    assert not qa._validate_claims({'claims':[{'text':'no values','evidence_ids':[a['id']]}]},evidence)
    assert not qa._validate_claims({'claims':[{'text':a['text'],'evidence_ids':[a['id'],evidence[1]['id']]}]},evidence)


def test_draft_id_is_never_accepted_as_factual_evidence(client,region):
    s=region[2];r=revision(client,s)
    result=client.post('/api/ask',json=body(region,table_sources=[{'source_id':s['id'],'revision_id':r['id']}])).json();draft=result['table_drafts'][0]
    assert not qa._validate_claims({'claims':[{'text':'987654321.123','evidence_ids':[draft['id']]}]},result['evidence'])


def test_ordinary_generation_keeps_existing_non_table_prompt(monkeypatch):
    import asyncio
    async def fake(system,payload,**kwargs):
        assert 'table_cell' not in system and 'source_kind' not in payload['evidence'][0]
        return {'claims':[],'missing_information':[]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    asyncio.run(qa._generate('Describe the evidence',[{'id':'ordinary','document_id':'d','title':'SYNTHETIC','page':1,'text':'SYNTHETIC ordinary evidence.'}]))
