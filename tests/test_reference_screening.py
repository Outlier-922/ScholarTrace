import copy
import pymupdf
import pytest
from conftest import upload_pdf, wait_for_document
from test_literature import atom
from test_reference_discovery import scope,graph_papers
from backend.app import literature,literature_store as repo,reference_screening,store

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
    assert screened['screening']['decisions'][0]['identity_clues']==ctx['rows'][0]['identity_clues'][screened['items'][0]['key']]
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


def test_suggestions_distinguish_background_from_method_and_abstain_on_ambiguity():
    item = {'key': 'arxiv:fixture'}
    link = {'id': 'cite', 'purpose_hint': {'purpose': 'background', 'quote': 'For further reading, see [1]'}}
    row = {'unit': {'id': 'unit'}, 'candidate_keys': [item['key']], 'body_citations': [link],
           'identity_clues': {item['key']: {'identifier': 'matched', 'title_literal_match': True,
                                            'first_author_surname_match': True}}}
    suggestion, = reference_screening.suggest_decisions([row], [item])
    assert suggestion['decision'] == 'exclude' and suggestion['quote'] == link['purpose_hint']['quote']
    assert suggestion['identity'] == 'uncertain'
    row['body_citations'].append({'id': 'second', 'purpose_hint': None})
    assert reference_screening.suggest_decisions([row], [item])[0]['decision'] == 'hold'
    row['body_citations'].pop()
    link['purpose_hint']['purpose'] = 'method'
    assert reference_screening.suggest_decisions([row], [item])[0]['decision'] == 'hold'
    row['identity_clues'][item['key']]['identifier'] = 'revision_differs'
    assert reference_screening.suggest_decisions([row], [item]) == []


@pytest.mark.parametrize('method_occurrence',[False, True])
def test_automatic_downstream_filter_keeps_full_author_year_audit(client,tmp_path,method_occurrence):
    path = tmp_path / 'synthetic-background-filter.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((54,60),'SYNTHETIC Background Filter',fontsize=18)
        page.insert_text((54,110),'See Writer et al. (2026) for a survey.')
        if method_occurrence:
            page.insert_text((54,150),'The experiment follows Writer et al. (2026).')
        bibliography = pdf.new_page()
        bibliography.insert_text((54,60),'References',fontsize=18)
        bibliography.insert_text((54,110),'Fixture Writer, Fixture Second, and Fixture Third. 2026.')
        bibliography.insert_text((64,125),'Synthetic Graph Evidence: A survey, with examples. arXiv:2605.19283.')
        pdf.save(path)
    registered = literature.parse_arxiv(atom('2605.19283v1','Synthetic Graph Evidence: A survey, with examples','Fixture Writer'))['items'][0]
    repo.save_records([registered])
    doc = wait_for_document(client,upload_pdf(client,path)['id'])
    route = f"/api/documents/{doc['id']}/reference-discovery"
    units = client.get(route, params={'parse_run_id':doc['parse_run_id']}).json()['units']
    joined = next(unit for unit in units if '2605.19283' in unit['text'])
    assert 'Fixture Writer' in joined['text'] and len(joined['sources']) == 2
    response = client.post(route,json={'parse_run_id':doc['parse_run_id'],'unit_ids':[joined['id']],'filter_background':True})
    assert response.status_code == 200,response.text
    result = response.json()
    if method_occurrence:
        assert 'background_filter' not in result
        assert [i['key'] for i in result['items']] == [registered['key']]
    else:
        assert result['items'] == []
        audit = result['background_filter']
        assert audit['excluded'][0]['anchors'][0]['quote'] == 'Writer et al. (2026) for a survey'
        assert [i['key'] for i in repo.get_search(audit['origin_search_id'])['result']['items']] == [registered['key']]
        assert client.post('/api/literature/batches',json={'search_id':result['search_id'],'keys':[registered['key']]}).status_code == 422
