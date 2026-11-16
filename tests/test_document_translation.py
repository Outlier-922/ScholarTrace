"""Synthetic translations verify budget/state/source contracts, not linguistic quality."""
import asyncio
import io
import json
import os
import uuid
from pathlib import Path
import threading
import time
import zipfile

import httpx
import pymupdf
import pytest

from backend.app import document_translation as full, model_client, store, translation, workflow
from conftest import upload_pdf, wait_for_document
from test_workflow import papers, planned, start, wait, configure


def body(document,limit=None):
    value={'document_id':document['id'],'parse_run_id':document['parse_run_id'],'target_language':'zh'}
    if limit:
        blocks=store.get_structure(document['id'],parse_run_id=document['parse_run_id'])['blocks']
        value.update(scope='blocks',block_ids=[block['id'] for block in blocks if block['type']=='paragraph' and len(block['text'])>=10][:limit])
    return value


def plan(client,document,limit=None):
    response=client.post('/api/document-translations',json=body(document,limit));assert response.status_code==200,response.text
    return response.json()


def done(client,identity):
    until=time.monotonic()+15
    while time.monotonic()<until:
        job=client.get('/api/document-translations/'+identity).json()
        if job['status'] not in full.ACTIVE:return job
        time.sleep(.02)
    pytest.fail('document translation did not stop at its batch boundary')


def run(client,job,**options):
    response=client.post('/api/document-translations/'+job['id']+'/control',json={'action':'start' if job['status']=='planned' else 'retry' if job['status']=='failed' else 'resume','expected_revision':job['revision'],**options})
    assert response.status_code==200,response.text
    return done(client,job['id'])


def provider(monkeypatch,fail=False,model=None,*,status_code=None):
    configure(monkeypatch);monkeypatch.setenv('LLM_MODEL',model or 'document-fixture-'+uuid.uuid4().hex);calls=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            payload=json.loads(kwargs['json']['messages'][-1]['content']);calls.append(payload)
            if fail:raise httpx.ReadTimeout('synthetic private provider failure')
            if status_code:
                return httpx.Response(status_code, json={'error': 'synthetic private provider failure'},
                                      request=httpx.Request('POST',url))
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'translation':'合成译文：'+payload['text']})}}],
                'usage':{'prompt_tokens':100,'completion_tokens':50}},request=httpx.Request('POST',url))
    monkeypatch.setattr(model_client.httpx,'AsyncClient',FakeClient)
    return calls


def test_planning_requires_no_model_and_partial_exports_are_explicit(client,papers):
    job=plan(client,papers[0]);assert job['status']=='planned' and job['estimate']['uncached_requests']>0
    assert job['totals']['calls']==0 and not full._workers
    exported=client.get('/api/document-translations/'+job['id']+'/export')
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert archive.testzip() is None and set(archive.namelist())=={'translation.json','translation.md','translation.html'}
        value=json.loads(archive.read('translation.json'));assert value['status']=='planned'
        assert '尚未翻译' in archive.read('translation.md').decode() and '语义未核验' in archive.read('translation.html').decode()
        assert all(item['translation'] is None for item in value['items'])


@pytest.mark.parametrize('changes',[{'scope':'pages'},{'scope':'pages','page_start':2,'page_end':1},{'scope':'pages','page_start':1,'page_end':9999},{'scope':'blocks','block_ids':['foreign']},{'scope':'document','page_start':1},{'glossary':{'':'bad'}},{'unknown':'code'}])
def test_invalid_scope_rejected_before_any_generation(client,papers,monkeypatch,changes):
    async def forbidden(*args,**kwargs):raise AssertionError('No model during invalid planning')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    assert client.post('/api/document-translations',json={**body(papers[0]),**changes}).status_code==422


def test_cache_only_stops_without_fabricating_translation(client,papers,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Cache-only must never generate')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    job=plan(client,papers[0],2);result=run(client,job,allow_model=False)
    assert result['status']=='paused' and result['totals']['calls']==0 and result['counts']['pending']==2
    assert all(unit['translation'] is None for unit in full.repo.items(job['id']))


@pytest.mark.parametrize('quota',[{'max_model_calls':0,'max_output_tokens':3500},{'max_model_calls':1,'max_output_tokens':3499}])
def test_exhaustion_is_recorded_before_http(client,papers,monkeypatch,quota):
    calls=provider(monkeypatch);job=plan(client,papers[0],1)
    result=run(client,job,allow_model=True,**quota)
    assert result['status']=='budget_exhausted' and result['totals']['calls']==0 and not calls
    assert full.repo.items(job['id'])[0]['status']=='pending'


def test_explicit_batches_resume_without_retranslating_and_cache_replay(client,papers,monkeypatch):
    calls=provider(monkeypatch);job=plan(client,papers[0],2)
    first=run(client,job,allow_model=True,max_model_calls=1,max_output_tokens=3500)
    assert first['status']=='budget_exhausted' and len(calls)==1 and first['counts']['translated']==1
    checkpoint=full.repo.items(job['id'])[0]
    second=run(client,first,allow_model=True,max_model_calls=1,max_output_tokens=3500)
    assert second['status']=='completed' and len(calls)==2 and second['totals']['calls']==2
    assert full.repo.items(job['id'])[0]==checkpoint and second['totals']['reserved_output_tokens']==7000
    again=plan(client,papers[0],2);cached=run(client,again,allow_model=False,max_model_calls=0,max_output_tokens=0)
    assert cached['status']=='completed' and cached['counts']['cached']==2 and cached['totals']['calls']==0 and len(calls)==2


def test_whole_document_preserved_units_do_not_count_as_translations(client,papers,monkeypatch):
    calls=provider(monkeypatch);job=plan(client,papers[0])
    while job['status'] not in {'completed','completed_with_gaps'}:job=run(client,job,allow_model=True,max_model_calls=20,max_output_tokens=70000,max_units=1000)
    units=full.repo.items(job['id']);assert job['counts'].get('preserved',0)>0
    assert len(calls)==job['counts']['translated'] and all(unit['translation'] is None for unit in units if unit['status']=='preserved')
    assert sum(job['counts'].values())==job['total'] and all(unit['status'] in full.DONE for unit in units)
    page=client.get('/api/document-translations/'+job['id']+'?page=1').json();assert all(unit['page']==1 for unit in page['items'])


def test_layout_keeps_cells_and_bibliography_but_translates_numeric_prose_and_appendix(client, tmp_path, monkeypatch):
    path = tmp_path / 'synthetic-translation-layout.pdf'
    with pymupdf.open() as pdf:
        for _ in range(3):
            page = pdf.new_page(width=400, height=600)
            page.insert_text((60, 60), 'Synthetic fixture PDF')
        for y in (132, 156, 222):
            pdf[0].draw_line((72, y), (328, y))
        pdf.save(path)
    doc = wait_for_document(client, upload_pdf(client, path)['id'])
    calls = provider(monkeypatch)

    def save(page, entries):
        rows = [{'id': store.new_id('blk'), 'type': kind, 'text': text, 'bbox': bbox,
                 'order': i, 'metadata': {'extraction': 'native_pdf'}, 'section_path': sections}
                for i, (kind, text, bbox, sections) in enumerate(entries)]
        store.save_page(doc['id'], doc['parse_run_id'], page, 400, 600, 0, [0, 0, 400, 600], 'ready', rows)
        return [row['id'] for row in rows]

    table, cell, heading, body_text = save(1, [
        ('table_caption', 'Table 1: Example results.', [.18,.17,.82,.20], []),
        ('paragraph', 'Accuracy 78.5', [.23,.30,.45,.32], []),
        ('heading', '3.1\nExperimental Setup', [.18,.39,.72,.42], []),
        ('table_candidate', 'The synthetic model was tested under the stated conditions and reports 7 samples, 8 groups and 9 trials.', [.18,.43,.82,.50], []),
    ])
    _, reference = save(2, [
        ('heading', 'References', [.18,.12,.48,.16], ['References']),
        ('paragraph', 'Example Author. Synthetic Paper.', [.18,.19,.82,.24], ['References']),
    ])
    appendix, caption = save(3, [
        ('heading', 'Appendix: Results', [.18,.1,.54,.15], ['References','Appendix: Results']),
        ('figure_caption', 'Figure 1: Additional synthetic results.', [.18,.42,.82,.46], ['References','Appendix: Results']),
    ])

    job = plan(client, doc)
    assert job['estimate']['uncached_requests'] == 5
    final = run(client, job, allow_model=True, max_model_calls=5, max_output_tokens=17500)
    assert final['status'] == 'completed' and len(calls) == 5
    units = {unit['block_id']: unit for unit in full.detail(final['id'])['items']}
    for key in [table, heading, body_text, appendix, caption]:
        assert units[key]['status'] == 'translated'
    assert units[heading]['translation'].startswith('3.1\n')
    assert any(call['text'] == 'Experimental Setup' for call in calls)
    assert units[body_text]['source']['type'] == 'table_candidate'
    assert units[cell]['reason'] == '原表区文字原样保留'
    assert units[reference]['status'] == 'preserved'
    assert units[cell]['translation'] is units[reference]['translation'] is None


@pytest.mark.parametrize('status_code',[None,402])
def test_failed_request_never_retries_automatically_and_budget_is_retained(client,papers,monkeypatch,status_code):
    calls=provider(monkeypatch,fail=status_code is None,status_code=status_code);job=plan(client,papers[0],2);result=run(client,job,allow_model=True)
    assert result['status']=='failed' and len(calls)==1 and result['counts']['failed']==1
    assert result['totals']['calls']==result['totals']['unreported_usage']==1 and result['totals']['reserved_output_tokens']==3500
    assert 'synthetic private provider failure' not in json.dumps(result)
    if status_code==402:
        assert 'HTTP 402' in result['notice'] and full.repo.items(job['id'])[0]['error_code']=='model_payment_required'
    assert client.post('/api/document-translations/'+job['id']+'/control',json={'action':'resume','expected_revision':result['revision']}).status_code==409
    successes=provider(monkeypatch,model=os.environ['LLM_MODEL']);finished=run(client,result,allow_model=True)
    assert finished['status']=='completed' and len(successes)==2 and finished['totals']['calls']==3


def test_pause_and_cancellation_take_effect_at_block_boundary(client,papers,monkeypatch):
    calls=provider(monkeypatch);entered=threading.Event();release=threading.Event();original=translation.translate_block
    async def slow(*args,**kwargs):
        value=await original(*args,**kwargs);entered.set()
        while not release.is_set():await asyncio.sleep(.01)
        return value
    monkeypatch.setattr(translation,'translate_block',slow);job=plan(client,papers[0],2)
    response=client.post('/api/document-translations/'+job['id']+'/control',json={'action':'start','expected_revision':job['revision'],'allow_model':True});assert response.status_code==200
    assert entered.wait(3)
    current=client.get('/api/document-translations/'+job['id']).json()
    assert client.post('/api/document-translations/'+job['id']+'/control',json={'action':'pause','expected_revision':current['revision']}).status_code==200
    release.set();paused=done(client,job['id']);assert paused['status']=='paused' and len(calls)==1 and paused['counts']['translated']==1
    response=client.post('/api/document-translations/'+job['id']+'/control',json={'action':'cancel','expected_revision':paused['revision']})
    assert response.json()['status']=='cancelled' and len(calls)==1


def test_restart_recovers_to_paused_and_never_schedules_paid_work(client,papers):
    job=plan(client,papers[0],1);unit=full.repo.items(job['id'])[0];unit.update(status='running',attempts=1)
    full.repo.mutate(job['id'],lambda value:value.update(status='running'),unit=unit)
    full.start();result=full.repo.get(job['id'])
    assert result['status']=='paused' and full.repo.items(job['id'])[0]['status']=='pending' and job['id'] not in full._workers


def test_interruption_after_cache_save_reuses_cached_output(client,papers,monkeypatch):
    calls=provider(monkeypatch);original=translation.translate_block
    async def interrupted(*args,**kwargs):await original(*args,**kwargs);raise asyncio.CancelledError()
    monkeypatch.setattr(translation,'translate_block',interrupted)
    job=plan(client,papers[0],1);paused=run(client,job,allow_model=True)
    assert paused['status']=='paused' and len(calls)==1
    done_job=run(client,paused,allow_model=False)
    assert done_job['status']=='completed' and done_job['counts']['cached']==1 and len(calls)==1


def test_historical_run_remains_pinned_after_reprocess(client,papers):
    document=papers[0];job=plan(client,document,1)
    assert client.post('/api/documents/'+document['id']+'/reprocess',json={'ocr':False,'tables':False}).status_code==200
    updated=wait_for_document(client,document['id']);assert updated['parse_run_id']!=document['parse_run_id']
    view=client.get('/api/document-translations/'+job['id']+'?page=1').json()
    assert view['source']['parse_run_id']==document['parse_run_id'] and view['items']


def test_changed_pdf_or_model_and_stale_controls_are_blocked(client,papers,monkeypatch):
    provider(monkeypatch);job=plan(client,papers[0],1)
    monkeypatch.setenv('LLM_MODEL','different-model')
    assert client.post('/api/document-translations/'+job['id']+'/control',json={'action':'start','expected_revision':1,'allow_model':True}).status_code==409
    path=Path(store.get_internal_document(papers[0]['id'])['file_path']);raw=path.read_bytes()
    try:
        path.write_bytes(raw+b'changed')
        assert client.get('/api/document-translations/'+job['id']+'?page=1').status_code==409
        assert client.get('/api/document-translations/'+job['id']+'/export').status_code==409
    finally:path.write_bytes(raw)
    full.repo.mutate(job['id'],lambda value:None)
    assert client.post('/api/document-translations/'+job['id']+'/control',json={'action':'start','expected_revision':1}).status_code==409


def test_unsupported_long_block_is_not_silently_completed_as_translation(client,papers):
    doc=papers[0];selected=body(doc,1)['block_ids'][0]
    with store.connection() as conn:
        original=conn.execute('SELECT text FROM blocks WHERE id=?',(selected,)).fetchone()[0]
        conn.execute('UPDATE blocks SET text=? WHERE id=?',('long English text '*1000,selected))
    try:
        response=client.post('/api/document-translations',json={**body(doc),'scope':'blocks','block_ids':[selected]});job=response.json()
        assert job['counts']['blocked']==1 and job['estimate']['uncached_requests']==0
        result=run(client,job,allow_model=False);assert result['status']=='completed_with_gaps'
        assert full.repo.items(job['id'])[0]['translation'] is None
    finally:
        with store.connection() as conn:conn.execute('UPDATE blocks SET text=? WHERE id=?',(original,selected))


def test_research_task_waits_for_document_plan_before_downstream_tools(client,papers,monkeypatch):
    calls=provider(monkeypatch);document=papers[0]
    task=planned(client,[document],goal='翻译全文再生成写作框架',actions=['translation','writing'],translation={'document':{'document_id':document['id'],'parse_run_id':document['parse_run_id'],'scope':'pages','page_start':1,'page_end':1}})
    # The evidence stage is disabled explicitly here because the fixture goal
    # includes writing too; this test isolates the translation dependency.
    workflow.repo.mutate(task['id'],lambda value:value['steps'][0].update(model_allowed=False))
    start(client,task);paused=wait(client,task['id']);assert paused['status']=='paused'
    step=next(step for step in paused['steps'] if step['tool']=='translation');assert step['status']=='waiting' and paused['steps'][-1]['attempts']==0
    job=full.repo.get(step['result']['id'])
    while job['status'] not in {'completed','completed_with_gaps'}:job=run(client,job,allow_model=True,max_model_calls=20,max_output_tokens=70000)
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume'}).status_code==200
    finished=wait(client,task['id']);assert finished['status']=='completed_with_gaps' and finished['steps'][-1]['status']=='completed'
    assert finished['usage']['model']['calls']==0 and job['totals']['calls']==len(calls)
    with zipfile.ZipFile(io.BytesIO(client.get('/api/research-tasks/'+task['id']+'/export').content)) as archive:
        assert 'translations/document-plan.zip' in archive.namelist()


def test_document_deletion_removes_plans_and_unit_checkpoints(client,papers):
    document=papers[0];job=plan(client,document,1)
    assert client.delete('/api/documents/'+document['id']).status_code==200
    assert client.get('/api/document-translations/'+job['id']).status_code==404
    assert not full.repo.items(job['id'])


def test_source_bound_edits_preserve_model_candidate_and_cache(client,papers,monkeypatch):
    provider(monkeypatch);job=run(client,plan(client,papers[0],1),allow_model=True)
    unit=full.repo.items(job['id'])[0];original=unit['translation']
    text=original.replace('合成译文：','校订候选：')
    response=client.post('/api/document-translations/'+job['id']+'/edits',json={'expected_revision':job['revision'],'block_id':unit['block_id'],'source_sha256':unit['source_sha256'],'text':text,'reason':'明确虚构用例的校订说明'})
    assert response.status_code==200,response.text
    updated=full.repo.items(job['id'])[0]
    assert updated['translation']==original and updated['reviewed_translation']==text and updated['edits'][0]['semantic_verified'] is False
    from backend.app import research_store
    assert research_store.get_translation(unit['cache_key'])['translated_text']==original
    markdown=client.get('/api/document-translations/'+job['id']+'/export?format=markdown').text
    assert text in markdown and '校订候选（未独立核验）' in markdown
    assert client.post('/api/document-translations/'+job['id']+'/edits',json={'expected_revision':job['revision'],'block_id':unit['block_id'],'source_sha256':unit['source_sha256'],'text':text,'reason':'旧版本重复提交'}).status_code==409
    current=full.repo.get(job['id'])
    assert client.post('/api/document-translations/'+job['id']+'/edits',json={'expected_revision':current['revision'],'block_id':unit['block_id'],'source_sha256':unit['source_sha256'],'text':text+' 999','reason':'不得增加数字'}).status_code==422
