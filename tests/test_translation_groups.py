"""Synthetic continuation, scope, provenance, budget and export contracts."""
import json
import zipfile
import io
import pytest
from fastapi import HTTPException
from backend.app import translation_groups as groups, document_translation as full, store
from test_workflow import papers
from test_document_translation import provider, run


def blocks():
    return [
        {'id':'left','page':1,'type':'paragraph','text':'We evaluated the compact student on a synthetic benchmark using the reference model as a',
         'bbox':[.15,.8,.85,.9],'metadata':{'extraction':'native_pdf'}},
        {'id':'table','page':2,'type':'paragraph','text':'A B C\n-1.23\n-2.34\n-3.45','bbox':[.1,.1,.9,.2],'metadata':{'extraction':'native_pdf'}},
        {'id':'right','page':2,'type':'paragraph','text':'teacher for an additional term in the loss. The controlled trial used 7 examples and lasted 9 hours.',
         'bbox':[.15,.25,.85,.35],'metadata':{'extraction':'native_pdf'}},
    ]


@pytest.mark.parametrize('change', ['none','complete','uppercase','ocr','columns','section','too_long'])
def test_only_strong_bounded_continuations_are_grouped(change):
    rows=blocks()
    if change=='complete':rows[0]['text']+=' complete sentence.'
    if change=='uppercase':rows[2]['text']='Teacher'+rows[2]['text'][7:]
    if change=='ocr':rows[2]['metadata']['extraction']='rapidocr'
    if change=='columns':rows[2]['bbox']=[.6,.2,.95,.3]
    if change=='section':rows.insert(1,{'id':'heading','page':2,'type':'heading','text':'5 Conclusion'})
    if change=='too_long':rows[2]['text']+=' word'*3000
    assert bool(groups.pairs(rows,{'left','right'}))==(change=='none')
    assert not groups.pairs(rows,{'left'})
    assert not groups.pairs(rows,{'right'})


@pytest.fixture
def grouped_source(client,papers,monkeypatch):
    document=dict(papers[0]);base=store.get_structure(document['id'],parse_run_id=document['parse_run_id']);rows=blocks()
    rows[0]['id']=base['blocks'][0]['id'];rows[2]['id']=base['blocks'][1]['id']
    document['_test_group_ids']=[rows[0]['id'],rows[2]['id']]
    def structure(doc,page=None,parse_run_id=None):
        assert doc==document['id'] and parse_run_id==document['parse_run_id']
        return {**base,'blocks':[b for b in rows if page is None or b['page']==page]}
    monkeypatch.setattr(store,'get_structure',structure)
    return document,rows


def plan(client,document,**options):
    r=client.post('/api/document-translations',json={'document_id':document['id'],'parse_run_id':document['parse_run_id'],
         'scope':'blocks','block_ids':document['_test_group_ids'],**options})
    assert r.status_code==200,r.text
    return r.json()


def test_group_is_one_request_with_two_source_anchors_and_cache_replay(client,grouped_source,monkeypatch):
    document,rows=grouped_source;calls=provider(monkeypatch)
    job=plan(client,document)
    assert job['estimate']['uncached_requests']==1 and job['counts']['linked']==1
    finished=run(client,job,allow_model=True,max_model_calls=1,max_output_tokens=3500)
    assert finished['status']=='completed' and len(calls)==1
    assert 'as a teacher' in calls[0]['text']
    first=full.detail(job['id'],1)['items'][0]
    assert [b['id'] for b in first['translation_sources']]==document['_test_group_ids']
    second=full.detail(job['id'],2)['items'][0]
    assert second['linked_source']['id']==rows[0]['id'] and second['translation'] is None
    again=run(client,plan(client,document),allow_model=False,max_model_calls=0,max_output_tokens=0)
    assert again['status']=='completed' and again['totals']['calls']==0 and len(calls)==1
    with zipfile.ZipFile(io.BytesIO(full.export(job['id']).body)) as archive:
        saved=json.loads(archive.read('translation.json'))
        assert saved['items'][0]['group_text']==groups.combined([rows[0],rows[2]])['text']
        assert '跨页续段' in archive.read('translation.md').decode()


def test_group_opt_out_budget_and_source_changes_are_not_hidden(client,grouped_source,monkeypatch):
    document,rows=grouped_source;calls=provider(monkeypatch)
    separate=plan(client,document,join_continuations=False)
    grouped=plan(client,document)
    assert separate['estimate']['uncached_requests']==2
    assert separate['manifest_digest']!=grouped['manifest_digest']
    exhausted=run(client,grouped,allow_model=True,max_model_calls=0,max_output_tokens=0)
    assert exhausted['status']=='budget_exhausted' and not calls
    primary=full.repo.items(grouped['id'])[0]
    rows[2]['text']+=' Changed after planning.'
    with pytest.raises(HTTPException):full.checked_block(grouped,primary)
