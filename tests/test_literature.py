"""Discovery protocol and lifecycle tests with explicitly synthetic upstream metadata."""
from __future__ import annotations
import asyncio
import io
import json
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from conftest import wait_for_document


def atom(identity='2601.12345v1',title='Synthetic Graph Evidence',author='Fixture Author'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:op="http://a9.com/-/spec/opensearch/1.1/">
    <op:totalResults>1</op:totalResults><entry><id>http://arxiv.org/abs/{identity}</id><title>{title}</title>
    <author><name>{author}</name></author><published>2026-01-02T00:00:00Z</published><updated>2026-02-01T00:00:00Z</updated>
    <summary>Clearly fictional metadata for software validation. Graph evidence retrieval.</summary></entry></feed>'''.encode()


def crossref():
    return json.dumps({'status':'ok','message':{'total-results':1,'items':[{'DOI':'10.1234/fixture','title':['Synthetic Graph Evidence'],
             'author':[{'given':'Fixture','family':'Author'}],'published':{'date-parts':[[2026,1,2]]},'abstract':'<p>Fictional <b>graph</b> metadata</p>',
             'link':[{'URL':'http://127.0.0.1/private','content-type':'application/pdf'}]}]}}).encode()


@pytest.mark.parametrize('text,expected',[
    ('https://arxiv.org/abs/1706.03762v7',('arxiv','1706.03762v7')),
    ('arXiv: hep-th/9901001v2',('arxiv','hep-th/9901001v2')),
    ('https://doi.org/10.1234/ABC',('crossref','10.1234/abc')),
    ('http://localhost/1706.03762v7',None),('1706.03762v7?evil=1',None)])
def test_identifiers_are_exact_and_not_arbitrary_urls(text,expected):
    from backend.app.literature import identify
    assert identify(text)==expected


def test_metadata_parsers_keep_registry_identity_without_trusting_fulltext_links():
    from backend.app.literature import parse_arxiv,parse_crossref
    paper=parse_arxiv(atom())['items'][0]
    assert paper['identifier']=='2601.12345v1' and paper['root_key']=='arxiv:2601.12345'
    assert paper['pdf_url']=='https://arxiv.org/pdf/2601.12345v1'
    book=parse_crossref(crossref())['items'][0]
    assert book['pdf_url'] is None and book['fulltext_status']=='metadata_only'
    assert '<b>' not in book['abstract']
    with pytest.raises(ValueError):parse_arxiv(b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///private">]><feed/>')


def test_search_is_cached_but_feedback_is_reapplied_and_mismatches_stay_visible(client,monkeypatch):
    from backend.app import literature
    calls=[]
    async def injected(provider,params=None,doi=None):
        calls.append(provider)
        return atom() if provider=='arxiv' else crossref()
    monkeypatch.setattr(literature,'_fetch',injected)
    body={'query':'synthetic graph '+uuid.uuid4().hex,'author':'Not The Author','year':2025}
    first=client.post('/api/literature/search',json=body).json()
    assert len(first['items'])==2
    arxiv=next(item for item in first['items'] if item['provider']=='arxiv')
    assert arxiv['identity_checks']['author_match'] is False
    assert arxiv['identity_checks']['year_match'] is False
    assert client.post('/api/literature/feedback',json={'key':arxiv['key'],'value':-1}).status_code==200
    second=client.post('/api/literature/search',json=body).json()
    assert len(calls)==2 and all(item['cache']=='hit' for item in second['providers'])
    assert all(item['key']!=arxiv['key'] for item in second['items'])
    included=client.post('/api/literature/search',json={**body,'include_disliked':True}).json()
    assert next(item for item in included['items'] if item['key']==arxiv['key'])['feedback']==-1
    client.post('/api/literature/feedback',json={'key':arxiv['key'],'value':0})


def test_exact_id_does_not_accept_a_different_version(client,monkeypatch):
    from backend.app import literature
    async def injected(*args,**kwargs):return atom('2601.12345v2')
    monkeypatch.setattr(literature,'_fetch',injected)
    response=client.post('/api/literature/search',json={'query':'2601.12345v9'})
    assert response.status_code==200 and response.json()['items']==[]
    assert len(response.json()['providers'])==1


def test_title_is_one_phrase_instead_of_independent_stopword_constraints(client,monkeypatch):
    from backend.app import literature
    observed=[]
    async def injected(provider,params=None,doi=None):
        observed.append(params['search_query'])
        return atom()
    monkeypatch.setattr(literature,'_fetch',injected)
    response=client.post('/api/literature/search',json={'query':'Attention Is All You Need','provider':'arxiv','mode':'title'})
    assert response.status_code==200
    assert observed==['ti:"Attention Is All You Need"']


def test_provider_failure_is_not_reported_as_successful_empty_search(client,monkeypatch):
    from backend.app import literature
    async def injected(provider,*args,**kwargs):
        if provider=='arxiv':
            raise httpx.HTTPStatusError('fixture',request=httpx.Request('GET','https://example.test'),response=httpx.Response(429))
        raise httpx.ConnectError('synthetic connection failure')
    monkeypatch.setattr(literature,'_fetch',injected)
    result=client.post('/api/literature/search',json={'query':'failure '+uuid.uuid4().hex}).json()
    assert result['items']==[]
    assert [item['status'] for item in result['providers']]==['rate_limited','failed']


def test_import_requires_confirmation_and_uses_existing_parser_and_hash_dedup(client,sample_pdfs,monkeypatch):
    from backend.app import literature,literature_store
    record=literature.parse_arxiv(atom('2602.12345v1'))['items'][0]
    literature_store.save_records([record])
    async def injected(identity):
        assert identity=='2602.12345v1'
        return io.BytesIO(sample_pdfs['v1'].read_bytes()),'https://arxiv.org/pdf/'+identity
    monkeypatch.setattr(literature,'download_pdf',injected)
    assert client.post('/api/literature/import',json={'key':record['key'],'confirm_identity':False}).status_code==422
    first=client.post('/api/literature/import',json={'key':record['key'],'confirm_identity':True})
    assert first.status_code==200,first.text
    document=wait_for_document(client,first.json()['document']['id'])
    assert document['status']=='ready'
    second=client.post('/api/literature/import',json={'key':record['key'],'confirm_identity':True}).json()
    assert second['document']['id']==document['id'] and second['document']['upload_outcome']=='reused'
    provenance=client.get(f"/api/documents/{document['id']}/provenance").json()['items']
    assert len(provenance)==1 and provenance[0]['sha256']==document['sha256']
    cross=literature.parse_crossref(crossref())['items'][0];literature_store.save_records([cross])
    assert client.post('/api/literature/import',json={'key':cross['key'],'confirm_identity':True}).status_code==422
    client.delete(f"/api/documents/{document['id']}")
    assert client.get(f"/api/documents/{document['id']}/provenance").status_code==404


@pytest.mark.parametrize('location',['http://127.0.0.1/private','https://arxiv.org.evil.test/pdf/2601.12345v1','https://arxiv.org/pdf/2601.99999v1','https://arxiv.org/pdf/2601.12345v1?secret=1'])
def test_pdf_redirect_cannot_leave_official_host_or_change_identity(monkeypatch,location):
    from backend.app import literature
    observed=[]
    class FakeClient:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,method,url,**kwargs):
            observed.append(url)
            yield httpx.Response(302,headers={'location':location},request=httpx.Request(method,url))
    monkeypatch.setattr(literature.httpx,'AsyncClient',FakeClient)
    with pytest.raises(ValueError):asyncio.run(literature._download_pdf('2601.12345v1'))
    assert observed==['https://arxiv.org/pdf/2601.12345v1']


def test_tracking_baseline_versions_nochange_and_failures_do_not_advance_success(client,monkeypatch):
    from backend.app import literature,literature_store
    topic='Synthetic tracking '+uuid.uuid4().hex
    item=client.post('/api/literature/subscriptions',json={'query':topic,'enabled':False}).json()
    again=client.post('/api/literature/subscriptions',json={'query':topic.upper(),'enabled':False}).json()
    assert again['id']==item['id']
    version=['v1']
    async def injected(*args,**kwargs):
        if version[0]=='fail':raise httpx.ConnectError('injected')
        return literature.parse_arxiv(atom('2603.12345'+version[0]))
    monkeypatch.setattr(literature,'search_provider',injected)
    route=f"/api/literature/subscriptions/{item['id']}"
    baseline=client.post(route+'/check').json()
    assert baseline['status']=='baseline' and baseline['items'][0]['change']=='baseline'
    assert 'window_items' not in baseline
    assert client.post(route+'/check').json()['status']=='no_change'
    latest=client.get(route+'/checks').json()
    assert latest['items'][0]['items']==[]
    assert latest['recommendations']['items'][0]['identifier']=='2603.12345v1'
    version[0]='v2'
    updated=client.post(route+'/check').json()
    assert updated['status']=='updated' and updated['items'][0]['change']=='new_version'
    success=literature_store.subscription(item['id'])['last_success_at']
    version[0]='fail'
    assert client.post(route+'/check').json()['status']=='failed'
    assert literature_store.subscription(item['id'])['last_success_at']==success
    latest=client.get(route+'/checks').json()
    assert len(latest['items'])==4
    assert latest['recommendations']['checked_at']==success


def test_tracking_ranks_local_topics_and_declared_code_and_reapplies_feedback(client,monkeypatch):
    from backend.app import literature, literature_store
    records = [
        literature.parse_arxiv(atom('2605.10001v1','Graph Evidence Baseline'))['items'][0],
        literature.parse_arxiv(atom('2605.10002v1','Graph Evidence Quantization Kernels').replace(
            b'</summary>', b' Code is available at https://github.com/example/fixture.</summary>'
        ))['items'][0],
    ]
    literature_store.save_records(records)

    async def injected(*args, **kwargs):
        return {'items': records}

    monkeypatch.setattr(literature, 'search_provider', injected)
    monkeypatch.setattr(literature.store, 'list_documents', lambda: [
        {'status': 'ready', 'title': 'Quantization Kernels'},
        {'status': 'failed', 'title': 'Graph Evidence Baseline'},
    ])
    topic = client.post('/api/literature/subscriptions', json={'query': 'graph evidence ' + uuid.uuid4().hex}).json()
    path = f"/api/literature/subscriptions/{topic['id']}"
    assert client.post(path+'/check').json()['status'] == 'baseline'
    ranked = client.get(path+'/checks').json()['recommendations']['items']
    preferred = ranked[0]
    assert preferred['key'] == records[1]['key']
    assert preferred['code_url'] == 'https://github.com/example/fixture'
    assert any('Quantization Kernels' in reason for reason in preferred['reasons'])
    try:
        assert client.post('/api/literature/feedback', json={'key':preferred['key'],'value':-1}).status_code == 200
        assert [item['key'] for item in client.get(path+'/checks').json()['recommendations']['items']] == [records[0]['key']]
        complete = client.get(path+'/checks?include_disliked=true').json()
        assert len(complete['items'][0]['items']) == 2
        assert next(item for item in complete['recommendations']['items'] if item['key']==preferred['key'])['feedback'] == -1
    finally:
        literature_store.set_feedback(preferred['key'],0)


@pytest.mark.parametrize('text', [
    'Background resources https://github.com/example/fixture',
    'Code is available at https://github.com.evil.test/example/fixture',
    'Code is available at https://github.com/example/fixture/issues',
])
def test_code_signal_requires_an_explicit_repository_link(text):
    from backend.app.literature import github_code_link
    assert github_code_link(text) is None


def test_tracking_code_verification_uses_public_registry_and_changes_ranking(client,monkeypatch):
    from backend.app import literature, literature_code
    github_names = ['missing-fixture-repo', 'licensed-fixture-repo']
    records = []
    for i,name in enumerate(github_names):
        seed=atom(f'2607.1000{i}v1','Synthetic Graph Evidence')
        records.extend(literature.parse_arxiv(seed.replace(
            b'</summary>', f' Code is available at https://github.com/scholartrace-fixture/{name}.</summary>'.encode()
        ))['items'])

    async def topic_results(*args,**kwargs):
        return {'items':records}

    fetched = []
    async def github_metadata(url):
        fetched.append(url)
        if url.endswith(github_names[0]):
            return 'not_found', None
        return 'public', {'full_name':'scholartrace-fixture/'+github_names[1],
                          'private':False,'license':{'spdx_id':'MIT'}}

    monkeypatch.setattr(literature,'search_provider',topic_results)
    monkeypatch.setattr(literature_code,'_fetch',github_metadata)
    topic=client.post('/api/literature/subscriptions',json={'query':'graph evidence '+uuid.uuid4().hex}).json()
    route=f"/api/literature/subscriptions/{topic['id']}"
    assert client.post(route+'/check').status_code == 200
    assert client.get(route+'/checks').json()['recommendations']['items'][0]['key'] == records[0]['key']
    checked=client.post(route+'/verify-code').json()
    assert [record['status'] for record in checked['items']] == ['not_found','open_license_reported']
    assert all(url.startswith('https://api.github.com/repos/scholartrace-fixture/') for url in fetched)
    updated=client.get(route+'/checks').json()['recommendations']['items']
    assert updated[0]['key'] == records[1]['key'] and updated[0]['code_verification']['license_id'] == 'MIT'
    assert client.post(route+'/verify-code').json() == checked and len(fetched) == 2


def test_tracking_feedback_is_independent_per_profile_and_preserves_default(client,monkeypatch):
    from backend.app import literature,literature_store
    record=literature.parse_arxiv(atom('2608.10001v1','Synthetic Graph Evidence'))['items'][0]
    literature_store.save_records([record])

    async def topic_results(*args,**kwargs):
        return {'items':[record]}

    monkeypatch.setattr(literature,'search_provider',topic_results)
    topic=client.post('/api/literature/subscriptions',json={'query':'graph '+uuid.uuid4().hex}).json()
    path=f"/api/literature/subscriptions/{topic['id']}"
    assert client.post(path+'/check').status_code == 200
    name='Synthetic profile '+uuid.uuid4().hex
    left=client.post('/api/literature/profiles',json={'name':name}).json()
    right=client.post('/api/literature/profiles',json={'name':name+' second'}).json()
    assert client.post('/api/literature/profiles',json={'name':name.upper()}).json() == left
    assert client.post('/api/literature/feedback',json={'key':record['key'],'value':-1,'profile_id':left['id']}).status_code==200
    assert client.post('/api/literature/feedback',json={'key':record['key'],'value':1,'profile_id':right['id']}).status_code==200
    assert client.get(path+'/checks',params={'profile_id':left['id']}).json()['recommendations']['items']==[]
    right_rank=client.get(path+'/checks',params={'profile_id':right['id']}).json()['recommendations']['items']
    assert right_rank[0]['feedback']==1
    assert client.get(path+'/checks').json()['recommendations']['items'][0]['feedback']==0
    assert client.get(path+'/checks',params={'profile_id':'unregistered'}).status_code==404


def test_tracking_budget_window_can_show_results_outside_the_exact_topic(client,monkeypatch):
    from backend.app import literature
    papers = [
        literature.parse_arxiv(atom('2609.12345v1','Low rank methods for adaptation of transport systems'))['items'][0],
        literature.parse_arxiv(atom('2609.12346v1','Low-rank adaptation for language models'))['items'][0],
        literature.parse_arxiv(atom('2609.12347v1','Dynamic LoRA for language models'))['items'][0],
    ]
    async def topic_results(*args,**kwargs):
        return {'items':papers}

    monkeypatch.setattr(literature,'search_provider',topic_results)
    topic=client.post('/api/literature/subscriptions',json={'query':'low rank adaptation'}).json()
    path=f"/api/literature/subscriptions/{topic['id']}"
    assert client.post(path+'/check').status_code == 200
    filtered=client.get(path+'/checks').json()['recommendations']['items']
    assert [paper['key'] for paper in filtered] == [papers[1]['key'],papers[2]['key']]
    full=client.get(path+'/checks',params={'include_disliked':True}).json()['recommendations']['items']
    assert [paper['key'] for paper in full] == [papers[1]['key'],papers[2]['key'],papers[0]['key']]


def test_feedback_reasons_use_real_shared_terms_not_invented_code_signals(client):
    from backend.app import literature,literature_store
    liked=literature.parse_arxiv(atom('2604.12345v1'))['items'][0]
    literature_store.save_records([liked]);literature_store.set_feedback(liked['key'],1)
    candidate=literature.parse_arxiv(atom('2604.99999v1','Graph Methods for Evidence'))['items'][0]
    ranked=literature.decorate([candidate],literature.SearchRequest(query='graph evidence'))[0]
    assert any('点赞文献共享词' in item for item in ranked['reasons'])
    assert not any('复现成功' in item or '官方代码' in item for item in ranked['reasons'])
    literature_store.set_feedback(liked['key'],0)


def test_scheduler_only_checks_enabled_due_topics(monkeypatch):
    from backend.app import literature
    observed=[]
    monkeypatch.setattr(literature.repo,'subscriptions',lambda:[
        {'id':'due','enabled':True,'next_check_at':'2020-01-01'},
        {'id':'future','enabled':True,'next_check_at':'2100-01-01'},
        {'id':'paused','enabled':False,'next_check_at':'2020-01-01'}])
    async def check(identity):observed.append(identity)
    async def stop(*args):raise asyncio.CancelledError()
    monkeypatch.setattr(literature,'check_subscription',check)
    monkeypatch.setattr(literature.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):asyncio.run(literature.tracking_loop())
    assert observed==['due']
