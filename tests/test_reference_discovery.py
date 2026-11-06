import copy
import pytest
from test_graph import graph_papers
from test_literature import atom
from backend.app import literature, literature_store as repo, reference_discovery as discovery

@pytest.fixture
def scope(client,graph_papers,monkeypatch):
    record=literature.parse_arxiv(atom('2605.22222v1'))['items'][0]
    repo.save_records([record])
    monkeypatch.setattr(discovery.graph,'cached_records',lambda: [record])
    async def forbidden(*args,**kwargs):raise AssertionError('No network in reference expansion')
    monkeypatch.setattr(literature,'_fetch',forbidden)
    doc=graph_papers[0]
    result=client.get(f"/api/documents/{doc['id']}/reference-discovery",params={'parse_run_id':doc['parse_run_id']})
    assert result.status_code==200,result.text
    return doc,result.json()['units']

def test_saved_candidates_keep_original_source_and_feed_existing_batch_preview(client,scope):
    doc,units=scope
    result=client.post(f"/api/documents/{doc['id']}/reference-discovery",json={'parse_run_id':doc['parse_run_id'],'unit_ids':[u['id'] for u in units]})
    assert result.status_code==200,result.text
    value=result.json();trace=value['reference_discovery']
    assert trace['network_requests']==trace['model_calls']==0 and trace['parse_run_id']==doc['parse_run_id']
    assert len(value['items'])==1 and len(trace['rows'])==3
    assert {r['status'] for r in trace['rows']}=={'candidates','no_cached_identity','no_literal_identifier'}
    assert trace['rows'][0]['unit']==units[0]
    assert repo.get_search(value['search_id'])['result']==value
    preview=client.post('/api/literature/batches',json={'search_id':value['search_id'],'keys':[i['key'] for i in value['items']]})
    assert preview.status_code==200 and not preview.json()['confirmed']

@pytest.mark.parametrize('selection',[[],['foreign'],['x']*6,['x','x']])
def test_invalid_selection_never_saves_snapshot(client,scope,selection):
    doc,_=scope
    with discovery.store.connection() as conn:before=conn.execute('SELECT count(*) FROM literature_searches').fetchone()[0]
    assert client.post(f"/api/documents/{doc['id']}/reference-discovery",json={'parse_run_id':doc['parse_run_id'],'unit_ids':selection}).status_code==422
    with discovery.store.connection() as conn:assert conn.execute('SELECT count(*) FROM literature_searches').fetchone()[0]==before

def test_explicit_version_does_not_match_other_cached_version(client,scope,monkeypatch):
    doc,units=scope;chosen=copy.deepcopy(units[0]);chosen['text']='SYNTHETIC arXiv:2605.22222v999'
    monkeypatch.setattr(discovery.graph,'bibliography_units',lambda _: [chosen])
    result=client.post(f"/api/documents/{doc['id']}/reference-discovery",json={'parse_run_id':doc['parse_run_id'],'unit_ids':[chosen['id']]}).json()
    assert result['items']==[] and result['reference_discovery']['rows'][0]['status']=='no_cached_identity'

def test_foreign_run_and_deleted_source_rejected(client,scope,graph_papers):
    doc,units=scope
    assert client.post(f"/api/documents/{doc['id']}/reference-discovery",json={'parse_run_id':graph_papers[1]['parse_run_id'],'unit_ids':[units[0]['id']]}).status_code==404
    client.delete('/api/documents/'+doc['id'])
    assert client.get(f"/api/documents/{doc['id']}/reference-discovery",params={'parse_run_id':doc['parse_run_id']}).status_code==404
