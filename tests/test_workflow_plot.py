"""Fictional CSV tests check orchestration, never actual research performance."""
import asyncio
import hashlib
import io
import json
import subprocess
import sys
import zipfile

import pytest

from backend.app import studio, workflow, workflow_plot
from test_workflow import papers, planned, start, wait, configure


@pytest.fixture
def dataset(client):
    response=client.post('/api/studio/datasets/demo')
    assert response.status_code==200
    return response.json()


def options(dataset,**changes):
    return {'dataset_id':dataset['id'],'sha256':dataset['sha256'],
            'spec':studio.ChartSpec(x='model',y=['accuracy'],aggregation='mean',repetition='run',units={'accuracy':'%'},**changes).model_dump()}


def make(client,papers,dataset,**changes):
    return planned(client,papers,goal='根据 CSV 科学绘图，保留数据与规则',actions=['plot'],plot=options(dataset),**changes)


@pytest.mark.parametrize('selection',[None,{'spec':{'x':'unknown'}},{'sha256':'0'*64},{'spec':{'y':['accuracy','accuracy']}},{'spec':{'aggregation':'none'}}])
def test_scope_and_rules_rejected_before_model_call(client,papers,dataset,monkeypatch,selection):
    async def forbidden(*args,**kwargs):raise AssertionError('invalid CSV rules must precede model planning')
    monkeypatch.setattr(workflow.model_client,'chat_json',forbidden)
    selected=options(dataset)
    if selection:
        selected.update({key:value for key,value in selection.items() if key!='spec'})
        selected['spec'].update(selection.get('spec',{}))
    response=client.post('/api/research-tasks',json={'goal':'CSV 科学绘图','document_ids':[p['id'] for p in papers],'planner':'model',**({'plot':selected} if selection is not None else {})})
    assert response.status_code in {409,422},response.text


def test_unused_options_and_unsupported_code_are_rejected(client,papers,dataset):
    base={'goal':'整理原文证据','document_ids':[p['id'] for p in papers],'actions':['evidence'],'plot':options(dataset)}
    assert client.post('/api/research-tasks',json=base).status_code==422
    base.update(actions=['plot']);base['plot']['spec']['code']='run()'
    assert client.post('/api/research-tasks',json=base).status_code==422


@pytest.mark.parametrize('kind',['table','line','bar','radar','radar3d','scatter3d'])
def test_six_templates_run_inside_workflow(client,papers,dataset,kind):
    selected=options(dataset,chart_type=kind);spec=selected['spec']
    if kind=='line':spec.update(x='run',group_by='model',repetition=None,aggregation='none',x_kind='numeric')
    if kind in {'radar','radar3d'}:spec.update(y=['accuracy','latency_ms','energy_j'],normalization='minmax',directions={'accuracy':'higher','latency_ms':'lower','energy_j':'lower'})
    if kind=='scatter3d':spec.update(x='parameters_million',y=['accuracy','latency_ms'],repetition=None,aggregation='none',x_kind='numeric',group_by='model')
    task=planned(client,papers,goal='CSV 科学绘图',actions=['plot'],plot=selected)
    assert task['plot_spec']['preview']['used_records']==9 and task['plan_history'][0]['plot_digest']
    start(client,task);done=wait(client,task['id'])
    assert done['status']=='completed_with_gaps' and done['sources']==task['sources'] and done['budgets']==task['budgets']
    result=next(step['result'] for step in done['steps'] if step['tool']=='plot')
    assert result['sha256']==dataset['sha256'] and '自建演示数据，非实验结果' in result['caption']
    assert result['scientific_validity_verified'] is False and result['paper_results_inferred'] is False
    assert done['usage']['model']['calls']==done['usage']['metadata']['requests']==0
    assert result['statistics']==task['plot_spec']['preview']['statistics']
    assert workflow_plot.checked_files(result).is_dir()


def test_budget_resume_and_export_reproduces_exact_records(client,papers,dataset,tmp_path):
    task=make(client,papers,dataset,max_tool_calls=1);start(client,task);paused=wait(client,task['id'])
    assert paused['status']=='budget_exhausted' and paused['steps'][-1]['attempts']==0
    evidence=paused['steps'][0]['result']
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume','max_tool_calls':8}).status_code==200
    done=wait(client,task['id']);assert done['status']=='completed_with_gaps'
    assert done['steps'][0]['attempts']==1 and done['steps'][0]['result']==evidence
    response=client.get('/api/research-tasks/'+task['id']+'/export');assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert {'plot/render.py','plot/input.csv','plot/processed.csv','plot/figure.pdf','plot/result.json','plot/manifest.json'}<=set(archive.namelist())
        assert archive.read('plot/input.csv')==studio.demo_bytes()
        assert '自建演示' in archive.read('task.md').decode()
        archive.extractall(tmp_path)
    render=subprocess.run([sys.executable,str(tmp_path/'plot/render.py')],capture_output=True,text=True,timeout=45)
    assert render.returncode==0,render.stderr
    assert (tmp_path/'plot/processed.csv').read_bytes()==(tmp_path/'plot/reproduced/processed.csv').read_bytes()
    assert (tmp_path/'plot/caption.txt').read_bytes()==(tmp_path/'plot/reproduced/caption.txt').read_bytes()
    result=done['steps'][-1]['result'];(studio.repo.folder(result['id'])/'figure.png').write_bytes(b'corrupted fixture')
    assert client.get('/api/research-tasks/'+task['id']+'/export').status_code==409


@pytest.mark.parametrize('change',['raw','profile','renderer','delete'])
def test_change_after_plan_stops_before_chart_and_later_steps(client,papers,dataset,monkeypatch,change):
    task=planned(client,papers,goal='CSV 科学绘图与流程图',actions=['plot','diagram'],plot=options(dataset))
    if change=='raw':(studio.repo.folder(dataset['id'])/'input.csv').write_bytes(b'model,accuracy\nA,12\n')
    elif change=='renderer':monkeypatch.setattr(workflow_plot,'renderer_digest',lambda:'0'*64)
    else:
        with studio.repo.store.connection() as conn:
            if change=='delete':conn.execute('UPDATE studio_assets SET deleted=1 WHERE id=?',(dataset['id'],))
            else:
                altered={**dataset,'data_status':'user_data'}
                conn.execute('UPDATE studio_assets SET payload=? WHERE id=?',(json.dumps(altered),dataset['id']))
    start(client,task);done=wait(client,task['id'])
    assert done['status']=='failed' and done['steps'][-1]['attempts']==0
    plot=next(step for step in done['steps'] if step['tool']=='plot')
    assert plot['status']=='failed' and not studio.repo.get(plot['artifact_id'])
    assert done['sources']==task['sources'] and done['plot_spec']==task['plot_spec']


def test_interrupted_registered_chart_is_reused(client,papers,dataset,monkeypatch):
    execute=workflow.execute;seen=[]
    async def interrupted(payload,step):
        result=await execute(payload,step)
        if step['tool']=='plot':
            seen.append(result['id'])
            if len(seen)==1:raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(workflow,'execute',interrupted)
    task=make(client,papers,dataset);start(client,task);paused=wait(client,task['id']);assert paused['status']=='paused'
    assert client.post('/api/research-tasks/'+task['id']+'/control',json={'action':'resume'}).status_code==200
    done=wait(client,task['id']);assert done['status']=='completed_with_gaps' and seen[0]==seen[1]
    assert done['steps'][0]['attempts']==1


def test_plot_only_never_asks_model_and_proposal_cannot_invent_data(client,papers,dataset,monkeypatch):
    configure(monkeypatch)
    async def forbidden(*args,**kwargs):raise AssertionError('Plot-only task cannot generate model answers')
    monkeypatch.setattr(workflow.qa,'_generate',forbidden)
    task=make(client,papers,dataset,use_model=True);start(client,task);done=wait(client,task['id'])
    assert done['status']=='completed_with_gaps' and done['usage']['model']['calls']==0
    async def proposal(*args,**kwargs):return {'actions':['plot'],'dimensions':['hardware']}
    monkeypatch.setattr(workflow.model_client,'chat_json',proposal)
    proposed=planned(client,papers,goal='核对模型硬件',planner='model')
    assert all(step['tool']!='plot' for step in proposed['steps'])
