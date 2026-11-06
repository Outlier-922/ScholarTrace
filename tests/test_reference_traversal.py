import asyncio,copy
import pytest
from test_graph import graph_papers
from backend.app import literature,literature_store as repo,literature_handoff as handoff,reference_traversal as api
from test_literature import atom

@pytest.fixture
def seed(client,graph_papers,monkeypatch):
    record=literature.parse_arxiv(atom('2605.22222v1'))['items'][0]
    monkeypatch.setattr(api.graph,'cached_records',lambda:[record])
    doc=graph_papers[0];item=literature.parse_arxiv(atom('2605.11111v1'))['items'][0]
    item.update(provider='crossref',title=doc['title']);repo.save_records([item])
    r=handoff.confirm_local(handoff.LocalRequest(key=item['key'],document_id=doc['id'],parse_run_id=doc['parse_run_id'],sha256=doc['sha256'],confirm_identity=True))
    return r,graph_papers

def create(client,seed,**kwargs):
    r=client.post('/api/literature/traversals',json={'handoff_id':seed[0]['id'],**kwargs});assert r.status_code==200,r.text;return r.json()
def body(v,node=None):
    n=node or v['nodes'][0]
    u=api.reference_discovery.units(n['document_id'],n['parse_run_id'])['units']
    return {'revision':v['revision'],'node_id':n['id'],'unit_ids':[i['id'] for i in u]}

def test_shared_budget_is_durable_and_completed_node_not_repeated(client,seed,monkeypatch):
    v=create(client,seed,max_requests=1);calls=[]
    async def provider(*args,**kwargs):
        literature.request_guard.get()();calls.append(1);return {'items':[],'cache':'miss'}
    monkeypatch.setattr(literature,'search_provider',provider)
    r=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v));assert r.status_code==200,r.text
    done=r.json();assert done['requests_used']==1 and done['nodes'][0]['status']=='done' and calls==[1]
    again=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(done));assert again.json()['revision']==done['revision'] and calls==[1]
    exported=client.get('/api/literature/traversals/'+v['id']+'/export').json()
    assert exported['nodes']==done['nodes'] and done['nodes'][0]['search_id'] in exported['search_snapshots']
    assert client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v)).status_code==409

def test_child_must_come_from_parent_snapshot_and_cycles_blocked(client,seed):
    v=create(client,seed);v=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v)).json()
    assert client.post('/api/literature/traversals/'+v['id']+'/attach',json={'revision':v['revision'],'parent_node_id':v['nodes'][0]['id'],'handoff_id':seed[0]['id']}).status_code==422

def test_invalid_units_spend_nothing_and_do_not_claim_execution(client,seed):
    v=create(client,seed,max_requests=3)
    response=client.post('/api/literature/traversals/'+v['id']+'/expand',json={'revision':v['revision'],'node_id':v['nodes'][0]['id'],'unit_ids':['foreign']})
    assert response.status_code==422
    current=api.require(v['id']);assert current['requests_used']==0 and current['nodes'][0]['attempts']==0

def test_interrupt_keeps_reserved_budget_and_requires_explicit_retry(client,seed,monkeypatch):
    v=create(client,seed,max_requests=1)
    async def interrupted(*args,**kwargs):
        literature.request_guard.get()();raise asyncio.CancelledError()
    monkeypatch.setattr(literature,'search_provider',interrupted)
    with pytest.raises(asyncio.CancelledError):asyncio.run(api.expand(v['id'],api.Expand(**body(v))))
    after=api.require(v['id']);assert after['requests_used']==1 and after['nodes'][0]['status']=='failed'
    assert [u['id'] for u in after['nodes'][0]['selected_units']]==body(v)['unit_ids']
    async def forbidden(*args,**kwargs):raise AssertionError('No network beyond shared ledger')
    monkeypatch.setattr(literature,'search_provider',forbidden)
    done=asyncio.run(api.expand(v['id'],api.Expand(**body(after))));assert done['requests_used']==1 and done['nodes'][0]['status']=='done'

@pytest.mark.parametrize('limits',[{'max_depth':3},{'max_nodes':6},{'max_requests':4}])
def test_limits_rejected(client,seed,limits):
    assert client.post('/api/literature/traversals',json={'handoff_id':seed[0]['id'],**limits}).status_code==422

def test_depth_node_cap_and_same_work_cycle_on_attaching_confirmed_child(client,seed,monkeypatch):
    v=create(client,seed,max_depth=1,max_nodes=2)
    v=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v)).json();parent=v['nodes'][0]
    doc=seed[1][1];item=literature.parse_arxiv(atom('2605.44444v1'))['items'][0];item.update(provider='crossref',title=doc['title'])
    # Synthetic candidate snapshot allows testing attachment independently of ranking.
    saved=repo.get_search(parent['search_id']);saved['result']['items'].append(item)
    import json
    with api.store.connection() as c:c.execute('UPDATE literature_searches SET payload=? WHERE id=?',(json.dumps(saved['result']),parent['search_id']))
    receipt=handoff.confirm_local(handoff.LocalRequest(key=item['key'],search_id=parent['search_id'],document_id=doc['id'],parse_run_id=doc['parse_run_id'],sha256=doc['sha256'],confirm_identity=True))
    r=client.post('/api/literature/traversals/'+v['id']+'/attach',json={'revision':v['revision'],'parent_node_id':parent['id'],'handoff_id':receipt['id']});assert r.status_code==200,r.text
    v=r.json();assert v['nodes'][1]['depth']==1 and v['nodes'][1]['parent_node_id']==parent['id']
    assert client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v,v['nodes'][1])).status_code==409
    assert client.post('/api/literature/traversals/'+v['id']+'/attach',json={'revision':v['revision'],'parent_node_id':parent['id'],'handoff_id':receipt['id']}).status_code==409

def test_cycle_rejected_even_with_free_node_slots(client,seed):
    import json
    v=create(client,seed);v=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v)).json();parent=v['nodes'][0]
    saved=repo.get_search(parent['search_id']);saved['result']['items'].append(seed[0]['metadata'])
    with api.store.connection() as c:c.execute('UPDATE literature_searches SET payload=? WHERE id=?',(json.dumps(saved['result']),parent['search_id']))
    d=seed[1][0];receipt=handoff.confirm_local(handoff.LocalRequest(key=seed[0]['key'],search_id=parent['search_id'],document_id=d['id'],parse_run_id=d['parse_run_id'],sha256=d['sha256'],confirm_identity=True))
    r=client.post('/api/literature/traversals/'+v['id']+'/attach',json={'revision':v['revision'],'parent_node_id':parent['id'],'handoff_id':receipt['id']})
    assert r.status_code==409 and '作品' in r.json()['detail']

def test_budget_spent_by_parent_is_not_reset_for_child(client,seed,monkeypatch):
    import json
    calls=[]
    async def provider(*args,**kwargs):literature.request_guard.get()();calls.append(1);return {'items':[],'cache':'miss'}
    monkeypatch.setattr(literature,'search_provider',provider)
    v=create(client,seed,max_requests=1);v=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v)).json();parent=v['nodes'][0]
    doc=seed[1][1];item=literature.parse_arxiv(atom('2605.44444v1'))['items'][0];item.update(provider='crossref',title=doc['title'])
    saved=repo.get_search(parent['search_id']);saved['result']['items'].append(item)
    with api.store.connection() as c:c.execute('UPDATE literature_searches SET payload=? WHERE id=?',(json.dumps(saved['result']),parent['search_id']))
    r=handoff.confirm_local(handoff.LocalRequest(key=item['key'],search_id=parent['search_id'],document_id=doc['id'],parse_run_id=doc['parse_run_id'],sha256=doc['sha256'],confirm_identity=True))
    v=client.post('/api/literature/traversals/'+v['id']+'/attach',json={'revision':v['revision'],'parent_node_id':parent['id'],'handoff_id':r['id']}).json()
    v=client.post('/api/literature/traversals/'+v['id']+'/expand',json=body(v,v['nodes'][1])).json()
    assert v['requests_used']==1 and calls==[1] and v['nodes'][1]['status']=='done'
