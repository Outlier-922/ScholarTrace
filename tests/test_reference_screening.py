import copy
import pytest
from test_reference_discovery import scope,graph_papers
from backend.app import literature,literature_store as repo,store

@pytest.fixture
def candidates(client,scope):
    doc,units=scope
    r=client.post(f"/api/documents/{doc['id']}/reference-discovery",json={'parse_run_id':doc['parse_run_id'],'unit_ids':[u['id'] for u in units]})
    value=r.json();ctx=client.get('/api/literature/searches/'+value['search_id']+'/screening-context')
    assert ctx.status_code==200,ctx.text
    context=ctx.json();unit=next(r for r in context['rows'] if r['candidate_keys']);link=unit['body_citations'][0]
    decision={'key':value['items'][0]['key'],'unit_id':unit['unit']['id'],'identity':'confirmed','decision':'include','purpose':'method','reason':'SYNTHETIC: use as the method comparison for this research goal.', 'citation_id':link['id'],'quote':link['context']}
    return value,context,{'goal':'SYNTHETIC research comparison','decisions':[decision]}

def test_anchored_screening_is_new_snapshot_and_original_retained(client,candidates):
    value,ctx,body=candidates
    result=client.post('/api/literature/searches/'+value['search_id']+'/screen',json=body)
    assert result.status_code==200,result.text
    screened=result.json();assert screened['search_id']!=value['search_id'] and len(screened['items'])==1
    assert repo.get_search(value['search_id'])['result']==value
    anchor=screened['screening']['decisions'][0]['anchor']
    assert anchor['text'][anchor['quote_start']:anchor['quote_end']]==anchor['quote']
    assert client.get('/api/literature/searches/'+screened['search_id']+'/screening-export').json()['result']==screened
    assert screened['screening']['semantic_verified'] is False
    assert client.get('/api/literature/searches/'+value['search_id']+'/screenings').json()['items'][0]['id']==screened['search_id']
    assert client.post('/api/literature/batches',json={'search_id':screened['search_id'],'keys':[screened['items'][0]['key']]}).status_code==200

@pytest.mark.parametrize('mutation',['unknown_key','unknown_unit','foreign_citation','fake_quote','missing_marker','uncertain_identity','unknown_purpose','missing_anchor','short_reason','duplicate','missing'])
def test_invalid_include_cannot_become_adopted_candidate(client,candidates,mutation):
    value,ctx,body=candidates;body=copy.deepcopy(body);d=body['decisions'][0]
    if mutation=='unknown_key':d['key']='foreign'
    elif mutation=='unknown_unit':d['unit_id']='foreign'
    elif mutation=='foreign_citation':d['citation_id']='foreign'
    elif mutation=='fake_quote':d['quote']='SYNTHETIC fabricated source sentence'
    elif mutation=='missing_marker':d['quote']='Fictional software test.'
    elif mutation=='uncertain_identity':d['identity']='uncertain'
    elif mutation=='unknown_purpose':d['purpose']='unknown'
    elif mutation=='missing_anchor':d.update(citation_id=None,quote='')
    elif mutation=='short_reason':d['reason']='        '
    elif mutation=='duplicate':body['decisions']*=2
    elif mutation=='missing':body['decisions']=[]
    assert client.post('/api/literature/searches/'+value['search_id']+'/screen',json=body).status_code==422

@pytest.mark.parametrize('decision',['exclude','hold'])
def test_non_adopted_items_retained_in_audit_but_rejected_by_import_scope(client,candidates,decision):
    value,ctx,body=candidates;body=copy.deepcopy(body);d=body['decisions'][0];d.update(decision=decision,identity='uncertain',citation_id=None,quote='',purpose='unknown')
    result=client.post('/api/literature/searches/'+value['search_id']+'/screen',json=body).json()
    assert result['items']==[] and result['screening']['decisions'][0]['decision']==decision
    assert len(result['screening']['candidate_metadata'])==1
    assert client.post('/api/literature/batches',json={'search_id':result['search_id'],'keys':[d['key']]}).status_code==422
    assert client.get('/api/literature/local-candidates',params={'key':d['key'],'search_id':result['search_id']}).status_code==422

def test_missing_source_blocks_screening_but_history_stays_readable(client,candidates):
    value,ctx,body=candidates
    store.delete_document(ctx['document_id'])
    assert client.post('/api/literature/searches/'+value['search_id']+'/screen',json=body).status_code==404
    assert repo.get_search(value['search_id'])['result']==value
