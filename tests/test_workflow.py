"""Controlled software fixtures; no real provider calls or scientific evaluation."""
from __future__ import annotations
import asyncio
import io
import json
import threading
import time
import zipfile
import httpx
import pytest
from dotenv import dotenv_values
from backend.app import model_client,model_status,workflow
from scripts.configure_model import save_configuration
from conftest import upload_pdf,wait_for_document


@pytest.fixture
def papers(client,sample_pdfs):
    return [wait_for_document(client,upload_pdf(client,sample_pdfs[key])['id']) for key in ['v1','distinct']]


def planned(client,papers,**kwargs):
    body={'goal':'compare model architecture and objective; citation graph; writing outline; diagram','document_ids':[paper['id'] for paper in papers],**kwargs}
    response=client.post('/api/research-tasks',json=body);assert response.status_code==200,response.text
    return response.json()


def wait(client,identity,statuses=None):
    statuses=statuses or {'completed_with_gaps','completed','failed','paused','budget_exhausted','cancelled'}
    until=time.monotonic()+15
    while time.monotonic()<until:
        response=client.get('/api/research-tasks/'+identity);assert response.status_code==200,response.text
        task=response.json()
        if task['status'] in statuses:return task
        time.sleep(.02)
    pytest.fail('workflow timeout')


def start(client,task,**options):
    response=client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'start',**options})
    assert response.status_code==200,response.text


def test_offline_complex_task_produces_real_artifacts_and_exports(client,papers):
    task=planned(client,papers)
    assert task['status']=='planned' and all(step['result'] is None for step in task['steps'])
    assert [step['tool'] for step in task['steps']]==['evidence','comparison','graph','writing','diagram']
    start(client,task);finished=wait(client,task['id'])
    assert finished['status']=='completed_with_gaps',finished
    assert all(step['status']=='completed' for step in finished['steps'])
    assert finished['usage']['model']['calls']==0
    assert finished['usage']['tool_calls']==5
    source_runs={source['document_id']:source['parse_run_id'] for source in task['sources']}
    for evidence in finished['steps'][0]['result']['evidence']:assert source_runs[evidence['document_id']]==evidence['parse_run_id']
    exported=client.get('/api/research-tasks/'+task['id']+'/export')
    assert exported.status_code==200,exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert {'task.json','task.md','comparison.md','graph.json','writing-plan.md','references.bib','diagram/render.py','diagram/figure.pdf'}<=set(archive.namelist())
        assert all(run in archive.read('task.md').decode() for run in source_runs.values())


def test_tool_budget_returns_partial_then_resumes_without_redoing_completed_steps(client,papers):
    task=planned(client,papers,max_tool_calls=1);start(client,task);paused=wait(client,task['id'])
    assert paused['status']=='budget_exhausted' and paused['steps'][0]['status']=='completed'
    before=paused['steps'][0]['result']
    response=client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume','max_tool_calls':8})
    assert response.status_code==200,response.text
    finished=wait(client,task['id'])
    assert finished['status']=='completed_with_gaps'
    assert finished['steps'][0]['attempts']==1 and finished['steps'][0]['result']==before


def test_pause_waits_for_step_boundary_and_cancel_stops_future_steps(client,papers,monkeypatch):
    entered=threading.Event();release=threading.Event();original=workflow.execute
    async def delayed(payload,step):
        entered.set()
        while not release.is_set():await asyncio.sleep(.01)
        return await original(payload,step)
    monkeypatch.setattr(workflow,'execute',delayed)
    task=planned(client,papers,actions=['evidence','writing']);start(client,task)
    assert entered.wait(3)
    response=client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'pause'})
    assert response.json()['status']=='pause_requested'
    release.set();paused=wait(client,task['id'])
    assert paused['status']=='paused' and paused['steps'][0]['status']=='completed'
    assert paused['steps'][1]['status']=='waiting'
    cancelled=client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'cancel'}).json()
    assert cancelled['status']=='cancelled'
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume'}).status_code==409


def test_recovered_side_effect_reuses_deterministic_artifact_id(client,papers,monkeypatch):
    original=workflow.execute;ids=[];interrupted=False
    async def once(payload,step):
        nonlocal interrupted
        result=await original(payload,step)
        if step['tool']=='graph':
            ids.append(result['id'])
            if not interrupted:interrupted=True;raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(workflow,'execute',once)
    task=planned(client,papers,actions=['evidence','graph']);start(client,task);paused=wait(client,task['id'])
    assert paused['status']=='paused'
    assert paused['steps'][0]['attempts']==1
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume'}).status_code==200
    finished=wait(client,task['id'])
    assert finished['status']=='completed_with_gaps' and ids[0]==ids[1]
    assert finished['steps'][0]['attempts']==1


def test_reprocessing_does_not_move_planned_source_snapshots(client,papers):
    first=papers[0];task=planned(client,papers,actions=['evidence','comparison','writing','graph'])
    response=client.post('/api/documents/'+first['id']+'/reprocess',json={'ocr':False,'tables':False});assert response.status_code==200
    new=wait_for_document(client,first['id']);assert new['parse_run_id']!=first['parse_run_id']
    start(client,task);finished=wait(client,task['id'])
    assert finished['status']=='completed_with_gaps'
    runs={source['document_id']:source['parse_run_id'] for source in task['sources']}
    for step in finished['steps']:
        if step['tool']=='evidence':anchors=step['result']['evidence']
        elif step['tool']=='comparison':anchors=[anchor for row in step['result']['rows'] for cell in row['cells'] for anchor in cell['evidence']]
        elif step['tool']=='writing':anchors=step['result']['evidence']
        else:anchors=[]
        assert all(runs[anchor['document_id']]==anchor['parse_run_id'] for anchor in anchors)


def test_source_deletion_removes_task_details_and_exports(client,papers):
    task=planned(client,papers,actions=['evidence']);start(client,task);wait(client,task['id'])
    assert client.delete('/api/documents/'+papers[1]['id']).status_code in {200,204}
    assert client.get('/api/research-tasks/'+task['id']).status_code==404
    assert client.get('/api/research-tasks/'+task['id']+'/export').status_code==404
    assert not any(item['id']==task['id'] for item in client.get('/api/research-tasks').json()['items'])


def test_invalid_tools_and_foreign_parse_runs_rejected_before_execution(client,papers):
    assert client.post('/api/research-tasks',json={'goal':'delete all data','document_ids':[papers[0]['id']],'actions':['delete']}).status_code==422
    response=client.post('/api/comparisons',json={'document_ids':[paper['id'] for paper in papers],'parse_runs':{papers[0]['id']:papers[1]['parse_run_id']},'use_model':False})
    assert response.status_code==404
    assert 'graph' not in workflow.infer('比较 architecture，不要引用图谱')[0]
    assert 'graph' in workflow.infer('不要写作，构建引用图谱')[0]


def configure(monkeypatch,endpoint='https://provider.test/v1'):
    for key,value in {'LLM_BASE_URL':endpoint,'LLM_API_KEY':'synthetic-fixture-not-a-real-key','LLM_MODEL':'synthetic-model','LLM_API_PROFILE':'auto','LLM_REASONING_EFFORT':''}.items():monkeypatch.setenv(key,value)


def test_openai_profile_uses_completion_budget_without_fixed_temperature(monkeypatch):
    configure(monkeypatch,'https://api.openai.com/v1');seen={}
    class FakeClient:
        def __init__(self,**kwargs):seen.update(kwargs)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            seen.update(kwargs)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"status":"ok"}'}}],'usage':{'prompt_tokens':12,'completion_tokens':4}},request=httpx.Request('POST',url))
    monkeypatch.setattr(model_client.httpx,'AsyncClient',FakeClient)
    budget=model_client.CallBudget(1,1000)
    with model_client.metered(budget):
        assert asyncio.run(model_client.chat_json('JSON',{},max_tokens=100))['status']=='ok'
        with pytest.raises(ValueError,match='model_budget_exhausted'):asyncio.run(model_client.chat_json('JSON',{},max_tokens=100))
    assert seen['json']['max_completion_tokens']==100 and 'max_tokens' not in seen['json'] and 'temperature' not in seen['json']
    assert seen['follow_redirects'] is False and budget.calls==1
    assert budget.input_tokens==12 and budget.output_tokens==4 and budget.unreported_usage==0


def test_failed_model_request_still_consumes_reserved_budget(monkeypatch):
    configure(monkeypatch)
    class Broken:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):raise httpx.ConnectError('synthetic private body')
    monkeypatch.setattr(model_client.httpx,'AsyncClient',Broken)
    budget=model_client.CallBudget(1,1000)
    with model_client.metered(budget):
        with pytest.raises(httpx.ConnectError):asyncio.run(model_client.chat_json('JSON',{},max_tokens=600))
    assert budget.calls==1 and budget.reserved_output_tokens==600 and budget.unreported_usage==1


@pytest.mark.parametrize('status_code,error_code', [(401,'authentication_failed'), (402,'payment_required')])
def test_diagnostics_do_not_send_request_when_missing_and_never_expose_provider_body(client,monkeypatch,status_code,error_code):
    monkeypatch.setenv('LLM_API_KEY','')
    status=client.get('/api/model/status').json();assert not status['ready'] and status['last_check'] is None
    assert client.post('/api/model/check').json()['status']=='not_ready'
    configure(monkeypatch)
    async def denied(*args,**kwargs):
        response=httpx.Response(status_code,json={'secret':'synthetic-sensitive-provider-body'},request=httpx.Request('POST','https://provider.test'))
        raise httpx.HTTPStatusError('synthetic-sensitive-provider-body',request=response.request,response=response)
    monkeypatch.setattr(model_client,'chat_json',denied)
    response=client.post('/api/model/check')
    assert response.json()['error_code']==error_code
    assert 'synthetic-sensitive-provider-body' not in response.text and 'synthetic-fixture-not-a-real-key' not in response.text
    assert client.get('/api/model/status').json()['last_check']['status']=='failed'
    monkeypatch.setenv('LLM_API_KEY','synthetic-key-after-rotation')
    assert client.get('/api/model/status').json()['last_check'] is None


def test_local_key_wizard_preserves_settings_and_never_keeps_temp_copy(tmp_path):
    path=tmp_path/'.env';path.write_text('# preserved\nSCHOLARTRACE_ENABLE_OCR=true\nLLM_API_KEY=old-synthetic\n',encoding='utf-8')
    save_configuration(path,{'LLM_BASE_URL':'https://api.deepseek.com','LLM_MODEL':'deepseek-flash','LLM_API_KEY':'synthetic-fixture-key','LLM_API_PROFILE':'compatible','LLM_REASONING_EFFORT':''})
    result=dotenv_values(path)
    assert result['SCHOLARTRACE_ENABLE_OCR']=='true' and result['LLM_API_KEY']=='synthetic-fixture-key'
    assert '# preserved' in path.read_text(encoding='utf-8')
    assert list(tmp_path.iterdir())==[path]
    before=path.read_bytes()
    with pytest.raises(ValueError):save_configuration(path,{'LLM_API_KEY':'bad\ninjection'})
    assert path.read_bytes()==before


def test_model_proposal_cannot_add_unsupported_tools(client,papers,monkeypatch):
    configure(monkeypatch)
    async def unsafe(*args,**kwargs):return {'actions':['shell','delete'],'dimensions':['architecture']}
    monkeypatch.setattr(model_client,'chat_json',unsafe)
    task=planned(client,papers,planner='model',actions=['evidence'])
    assert task['planner_mode']=='rules' and task['warnings']
    assert [step['tool'] for step in task['steps']]==['evidence']


def test_disabling_model_prevents_calls_even_when_configured(client,papers,monkeypatch):
    configure(monkeypatch)
    async def forbidden(*args,**kwargs):raise AssertionError('Model must not be contacted')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    task=planned(client,papers,use_model=False);start(client,task)
    result=wait(client,task['id'])
    assert result['status']=='completed_with_gaps' and result['usage']['model']['calls']==0


def test_zero_model_budget_preserves_evidence_without_network(client,papers,monkeypatch):
    configure(monkeypatch)
    task=planned(client,papers,use_model=True,max_model_calls=0,actions=['evidence','comparison'])
    start(client,task);result=wait(client,task['id'])
    assert result['status']=='completed_with_gaps'
    assert result['usage']['model']['calls']==0 and result['usage']['model']['exhausted']
    assert result['steps'][0]['result']['evidence']
    assert any('预算' in gap for gap in result['gaps'])


def test_missing_retrieval_adds_only_one_bounded_supplement(client,papers):
    task=planned(client,papers,goal='qxzunmatchedkeyword',actions=['evidence'])
    start(client,task);result=wait(client,task['id'])
    assert result['query_expansions']==1 and len(result['steps'])==2
    assert result['steps'][0]['result']['uncovered_documents']
    assert result['steps'][1]['id']=='supplement'
    assert result['steps'][1]['result']['evidence']
    assert result['usage']['tool_calls']==2


def test_sqlite_recovery_preserves_completed_step_and_waits_for_resume(client,papers):
    task=planned(client,papers,max_tool_calls=1,actions=['evidence','writing']);start(client,task)
    before=wait(client,task['id']);assert before['status']=='budget_exhausted'
    def interrupted(payload):
        payload['status']='running';payload['steps'][1]['status']='running'
    workflow.repo.mutate(task['id'],interrupted)
    workflow.repo.recover()
    recovered=client.get('/api/research-tasks/'+task['id']).json()
    assert recovered['status']=='paused' and recovered['steps'][1]['status']=='waiting'
    assert recovered['steps'][0]['result']==before['steps'][0]['result']
    response=client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume','max_tool_calls':8})
    assert response.status_code==200,response.text
    finished=wait(client,task['id']);assert finished['status']=='completed_with_gaps'
    assert finished['steps'][0]['attempts']==1


def test_substantive_batch_passage_is_not_displaced_by_short_heading(monkeypatch):
    from backend.app import qa
    blocks=[
        {'id':'heading','page':1,'type':'heading','text':'Pre-training batch size sequence length','section_path':['Pre-training'],'bbox':[0,0,1,1]},
        {'id':'native_candidate','page':2,'type':'table_candidate','text':'We train the fictional model with a batch size of 257 sequences. Each sequence has 513 tokens. These invented values are for a software test only.','section_path':['Pre-training Procedure'],'bbox':[0,0,1,1]},
        {'id':'fine','page':3,'type':'paragraph','text':'For fine-tuning, the batch size is 17 and the sequence length is 65.','section_path':['Fine-tuning Procedure'],'bbox':[0,0,1,1]},
    ]
    monkeypatch.setattr(qa.store,'get_structure',lambda *args,**kwargs:{'parse_run_id':'fixture-run','blocks':blocks})
    monkeypatch.setattr(qa.semantic,'search',lambda *args,**kwargs:([],{'used':False}))
    hits=qa._retrieve('pre-training batch size and sequence length',[{'id':'fixture-doc','title':'Fictional test'}],1)
    assert hits[0]['block_id']=='native_candidate'


@pytest.mark.parametrize('end_heading',['Appendix A','Chapter 2','2 Another Chapter'])
def test_bibliography_year_heading_does_not_leak_and_appendix_or_chapter_is_retained(monkeypatch,end_heading):
    from backend.app import qa
    def block(identity,kind,text,path=None):
        return {'id':identity,'page':1,'type':kind,'text':text,'section_path':path or [],'bbox':[0,0,1,1]}
    blocks=[block('refs','heading','References'),block('year','heading','2019. Fictional reference continuation'),
            block('ref_gpu','paragraph','Reference only: training used 999 GPUs.', ['2019. Fictional reference continuation']),
            block('appendix','heading',end_heading),block('body','paragraph','Actual fictional training used 7 GPUs. This is a labelled software fixture.')]
    monkeypatch.setattr(qa.store,'get_structure',lambda *args,**kwargs:{'parse_run_id':'fixture-run','blocks':blocks})
    sources=qa._collect_sources([{'id':'fixture-doc','title':'Synthetic test'}])
    assert 'ref_gpu' not in {source['block_id'] for source in sources}
    assert 'body' in {source['block_id'] for source in sources}


def test_shutdown_does_not_reschedule_queued_tasks(client,papers,monkeypatch):
    entered=threading.Event();loops=[];calls=[]
    async def delayed(payload,step):
        loops.append(asyncio.get_running_loop());calls.append(payload['id'])
        if len(calls)==2:entered.set()
        while True:await asyncio.sleep(1)
    monkeypatch.setattr(workflow,'execute',delayed)
    tasks=[planned(client,papers,actions=['evidence']) for _ in range(3)]
    try:
        for task in tasks:start(client,task)
        assert entered.wait(3)
        asyncio.run_coroutine_threadsafe(workflow.shutdown(),loops[0]).result(timeout=5)
        time.sleep(.05)
        assert len(calls)==2 and not workflow._workers
        workflow.repo.recover()
        assert all(client.get('/api/research-tasks/'+task['id']).json()['status']=='paused' for task in tasks)
    finally:workflow.start()
