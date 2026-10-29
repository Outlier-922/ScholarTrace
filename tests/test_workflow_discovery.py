"""Discovery lifecycle and network authority through synthetic registry responses."""
import copy
import hashlib
import io
import json
import uuid
import zipfile

import httpx
import pytest

from backend.app import literature, literature_store, store, workflow
from test_literature import atom, crossref
from test_workflow import papers, planned, start, wait, configure


def spec(**changes):
    return {'query':'synthetic graph '+uuid.uuid4().hex,'provider':'arxiv','mode':'topic','network':'allow','limit':3,**changes}


def upstream(monkeypatch, fail=False):
    calls=[]
    async def fetch(provider,params=None,doi=None):
        if guard:=literature.request_guard.get():guard()
        calls.append(provider)
        if fail:raise httpx.ReadTimeout('synthetic private response')
        return atom()
    monkeypatch.setattr(literature,'_fetch',fetch)
    return calls


def run(client,papers,discovery,**options):
    task=planned(client,papers,goal='查找文献 Transformer model architecture',actions=['discovery'],discovery=discovery,**options)
    start(client,task)
    return task,wait(client,task['id'])


def expire(discovery):
    key=hashlib.sha256(json.dumps(['query-v2',discovery['provider'],discovery['query'],discovery['mode'],discovery['limit'],False],ensure_ascii=False).encode()).hexdigest()
    with store.connection() as conn:conn.execute('UPDATE literature_cache SET expires_at=? WHERE key=?',('2000-01-01T00:00:00+00:00',key))


def test_missing_query_scope_is_rejected_before_model_planning(client,papers,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('No model call for invalid options')
    monkeypatch.setattr(workflow.model_client,'chat_json',forbidden)
    response=client.post('/api/research-tasks',json={'goal':'查找相关论文','document_ids':[p['id'] for p in papers],'planner':'model'})
    assert response.status_code==422 and '检索条件' in response.text


@pytest.mark.parametrize('options',[{'query':'  '},{'provider':'private'},{'network':'automatic'},{'limit':6},{'year':1599},{'unknown':'ignored'}])
def test_invalid_discovery_spec_is_rejected(client,papers,options):
    response=client.post('/api/research-tasks',json={'goal':'查找文献','actions':['discovery'],'document_ids':[p['id'] for p in papers],'discovery':spec(**options)})
    assert response.status_code==422


def test_cache_only_miss_cannot_transmit_or_become_fulltext(client,papers,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Cache-only must never reach upstream')
    monkeypatch.setattr(literature,'_fetch',forbidden)
    before=store.stats();task,result=run(client,papers,spec(network='cache_only'))
    discovery=next(step['result'] for step in result['steps'] if step['tool']=='discovery')
    assert discovery['provider_status']['status']=='cache_miss' and discovery['items']==[]
    assert discovery['fulltext_imported'] is False and result['usage']['metadata']['requests']==0
    assert result['sources']==task['sources'] and store.stats()==before


def test_metadata_budget_stops_before_request_and_can_resume(client,papers,monkeypatch):
    calls=upstream(monkeypatch)
    task,result=run(client,papers,spec(),max_metadata_requests=0)
    assert result['status']=='budget_exhausted' and result['steps'][0]['status']=='waiting'
    assert not calls and result['usage']['metadata']['requests']==0
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume','max_metadata_requests':1}).status_code==200
    result=wait(client,task['id'])
    assert result['status']=='completed_with_gaps' and calls==['arxiv']
    assert result['usage']['metadata']['requests']==1 and result['usage']['model']['calls']==0


def test_source_conditions_filter_and_keep_excluded_candidates(client,papers,monkeypatch):
    upstream(monkeypatch)
    task,result=run(client,papers,spec(author='Another Author',year=2025))
    data=result['steps'][0]['result']
    assert data['items']==[] and len(data['excluded_candidates'])==1
    assert len(data['excluded_candidates'][0]['reasons'])==2
    assert data['excluded_candidates'][0]['record']['source_url'].startswith('https://export.arxiv.org/')
    saved=client.get('/api/literature/searches/'+data['search_id']).json()
    assert saved['result']['items']==[] and saved['result']['excluded_candidates']==data['excluded_candidates']
    assert saved['request']['author']=='Another Author'


def test_cache_reuse_expiration_and_archive_keep_provenance(client,papers,monkeypatch):
    calls=upstream(monkeypatch);options=spec(author='Fixture',year=2026)
    _,first=run(client,papers,options);assert len(calls)==1
    expire(options)
    task,cached=run(client,papers,{**options,'network':'cache_only'})
    result=cached['steps'][0]['result']
    assert result['provider_status']['cache']=='stale' and len(result['items'])==1
    assert cached['usage']['metadata']=={'requests':0,'cache_hits':1} and len(calls)==1
    assert any('过期' in warning for warning in result['warnings'])
    exported=client.get('/api/research-tasks/'+task['id']+'/export')
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        record=json.loads(archive.read('discovery/discovery-arxiv.json'))
        assert record['items'][0]['source_url']==first['steps'][0]['result']['items'][0]['source_url']
        assert '过期' in archive.read('discovery/discovery-arxiv.md').decode()


def test_provider_failure_preserves_failed_attempt_and_uses_stale_cache(client,papers,monkeypatch):
    options=spec();upstream(monkeypatch);run(client,papers,options);expire(options)
    calls=upstream(monkeypatch,fail=True);task,result=run(client,papers,options)
    failed,recovery=result['steps'][:2]
    assert result['status']=='completed_with_gaps' and failed['status']=='failed'
    assert failed['partial_result']['provider_status']['status']=='failed' and failed['recovery_status']=='local_completed'
    assert recovery['result']['provider_status']['cache']=='stale' and recovery['result']['items']
    assert result['execution_metadata_disabled'] and result['plan_history'][1]['reason']=='metadata_failed_cache_only'
    assert result['sources']==task['sources'] and result['budgets']==task['budgets']
    assert calls==['arxiv'] and result['usage']['metadata']['requests']==1
    assert 'synthetic private response' not in json.dumps(result)


@pytest.mark.parametrize('options',[{'recovery_policy':'stop'},{'max_replans':0}])
def test_failure_stop_policy_does_not_run_later_steps(client,papers,monkeypatch,options):
    calls=upstream(monkeypatch,fail=True);_,result=run(client,papers,spec(),**options)
    assert result['status']=='failed' and result['steps'][1]['attempts']==0 and calls==['arxiv']
    assert result['plan_version']==1 and result['steps'][0]['partial_result']


def test_failure_turns_remaining_provider_steps_to_cache_only(client,papers,monkeypatch):
    calls=upstream(monkeypatch,fail=True);_,result=run(client,papers,spec(provider='both'))
    assert result['status']=='completed_with_gaps' and calls==['arxiv']
    second=next(step for step in result['steps'] if step['id']=='discovery-crossref')
    assert second['result']['provider_status']['status']=='cache_miss'
    assert result['usage']['metadata']['requests']==1


def test_exact_identifier_routes_once_and_model_cannot_add_discovery(client,papers,monkeypatch):
    task=planned(client,papers,goal='查找文献',actions=['discovery'],discovery=spec(query='10.18653/v1/N19-1423',provider='both'))
    assert [step['metadata_provider'] for step in task['steps'] if step['tool']=='discovery']==['crossref']
    configure(monkeypatch)
    async def proposal(*args,**kwargs):return {'actions':['discovery'],'dimensions':['hardware']}
    monkeypatch.setattr(workflow.model_client,'chat_json',proposal)
    task=planned(client,papers,goal='比较硬件',planner='model')
    assert 'discovery' not in [step['tool'] for step in task['steps']]


def test_guard_context_does_not_leak_to_standalone_search(client,papers,monkeypatch):
    calls=upstream(monkeypatch);run(client,papers,spec())
    assert literature.request_guard.get() is None
    response=client.post('/api/literature/search',json={'query':'standalone '+uuid.uuid4().hex,'provider':'arxiv'})
    assert response.status_code==200 and len(calls)==2


def test_saved_search_unknown_returns_404(client):
    assert client.get('/api/literature/searches/missing-snapshot').status_code==404


def test_real_fetch_guard_counts_before_http_stream_and_resumes_second_provider(client,papers,monkeypatch):
    original=literature.httpx.AsyncClient;requests=[]
    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(200,content=atom() if request.url.host=='export.arxiv.org' else crossref())
    def factory(*args,**kwargs):return original(*args,**kwargs,transport=httpx.MockTransport(transport))
    monkeypatch.setattr(literature.httpx,'AsyncClient',factory)
    task,pending=run(client,papers,spec(provider='both'),max_metadata_requests=1)
    assert pending['status']=='budget_exhausted' and len(requests)==1
    assert pending['steps'][0]['status']=='completed' and pending['steps'][1]['status']=='waiting'
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume','max_metadata_requests':2}).status_code==200
    result=wait(client,task['id'])
    assert result['status']=='completed_with_gaps' and len(requests)==2
    assert result['usage']['metadata']['requests']==2


def test_offline_guard_blocks_even_a_broken_cache_helper(client,papers,monkeypatch):
    calls=upstream(monkeypatch)
    async def broken(provider,*args,**kwargs):return await literature._fetch(provider)
    monkeypatch.setattr(literature,'search_provider',broken)
    _,result=run(client,papers,spec(network='cache_only'))
    assert calls==[] and result['usage']['metadata']['requests']==0
    assert result['steps'][0]['result']['provider_status']['status']=='failed'
