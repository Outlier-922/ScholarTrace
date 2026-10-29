"""Frozen local CSV workflow/reproduction exercise, with no external requests."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def exercise(frozen,output):
    from backend.app import workflow,studio,store,workflow_plot,model_client,literature
    def forbidden(*args,**kwargs):raise AssertionError('Frozen exercise prohibits model/registry requests')
    model_client.chat_json=forbidden;literature._fetch=forbidden
    def save(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    async def run_control(value,action,**changes):
        await workflow.control(value['id'],workflow.Control(action=action,expected_revision=value['revision'],**changes))
        worker=workflow._workers.get(value['id'])
        if worker:await worker
        return workflow.repo.get(value['id'])
    studio.repo.init_db();workflow.repo.init_db();workflow.start();before=store.stats();cases=[]
    try:
        source=frozen['source'];document=store.get_document(source['document_id'])
        assert document['sha256']==source['sha256']
        structure=store.get_structure(source['document_id'],parse_run_id=source['parse_run_id'])
        digest=lambda:hashlib.sha256(json.dumps([{key:b[key] for key in ['id','text','page','bbox']} for b in structure['blocks']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        assert digest()==source['blocks_sha256']
        dataset=studio.create_dataset(studio.demo_bytes(),'stage26_synthetic_plotting_demo.csv','synthetic_demo');save('dataset.json',dataset)
        for name in ['bar_resume','radar','writing_chain']:
            selected=studio.ChartSpec(x='model',y=['accuracy'],aggregation='mean',repetition='run',error_bar='std',units={'accuracy':'%'},title='自建演示数据比较 · 非实验结果').model_dump()
            if name=='radar':selected.update(chart_type='radar',y=['accuracy','latency_ms','energy_j'],error_bar='none',normalization='minmax',directions={'accuracy':'higher','latency_ms':'lower','energy_j':'lower'},units={'accuracy':'%','latency_ms':'ms','energy_j':'J'})
            actions=['plot'] if name!='writing_chain' else ['writing','plot','diagram']
            goal='【CSV任务验证】'+name+'：根据明确规则绘图，演示数据不是论文实验'
            plan=await workflow.plan(workflow.PlanRequest(goal=goal,document_ids=[source['document_id']],actions=actions,
                plot={'dataset_id':dataset['id'],'sha256':dataset['sha256'],'spec':selected},
                use_model=False,max_tool_calls=1 if name=='bar_resume' else 8,max_model_calls=0,max_metadata_requests=0,max_output_tokens=600,max_active_seconds=180))
            save(name+'_plan.json',plan)
            done=await run_control(plan,'start')
            if name=='bar_resume':
                save(name+'_budget_stop.json',done);assert done['status']=='budget_exhausted' and done['steps'][-1]['attempts']==0
                evidence=done['steps'][0]['result'];done=await run_control(done,'resume',max_tool_calls=8)
                assert done['steps'][0]['attempts']==1 and done['steps'][0]['result']==evidence
            save(name+'.json',done)
            assert done['status']=='completed_with_gaps' and done['sources']==plan['sources'] and done['plot_spec']==plan['plot_spec']
            assert done['usage']['model']['calls']==done['usage']['metadata']['requests']==0
            result=next(step['result'] for step in done['steps'] if step['tool']=='plot')
            assert result['scientific_validity_verified'] is False and result['data_status']=='synthetic_demo'
            assert result['statistics']==plan['plot_spec']['preview']['statistics']
            folder=workflow_plot.checked_files(result)
            bundle=output/(name+'.zip');bundle.write_bytes(workflow.export(done['id']).body)
            with zipfile.ZipFile(bundle) as archive:
                assert archive.testzip() is None and json.loads(archive.read('task.json'))==done
                assert hashlib.sha256(archive.read('plot/input.csv')).hexdigest()==dataset['sha256']
                archive.extractall(output/(name+'_reproduction'))
            root=output/(name+'_reproduction')/'plot'
            rendered=subprocess.run([sys.executable,str(root/'render.py')],capture_output=True,text=True,encoding='utf-8',timeout=60)
            (output/(name+'_reproduction_log.txt')).write_text(rendered.stdout+rendered.stderr,encoding='utf-8')
            assert rendered.returncode==0
            assert (root/'processed.csv').read_bytes()==(root/'reproduced/processed.csv').read_bytes()
            assert (root/'caption.txt').read_bytes()==(root/'reproduced/caption.txt').read_bytes()
            cases.append({'name':name,'task_id':done['id'],'chart_id':result['id'],'status':done['status'],'usage':done['usage'],
                          'data_status':result['data_status'],'sha256':result['sha256'],'used_records':result['used_records'],
                          'statistics':result['statistics'],'normalizations':result['normalizations'],'zip_valid':True,'processed_and_caption_reproduced':True})
            save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,'external_requests':0,'scientific_quality_verified':False})
            print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
        assert store.stats()==before and digest()==source['blocks_sha256']
        save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,
                            'library_stats':before,'external_requests':0,'scientific_quality_verified':False})
    finally:await workflow.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['freeze','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Preserve first outputs; choose a new directory.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    assert frozen['external_request_cap']==0
    for name,digest in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()),SCHOLARTRACE_ENABLE_DENSE='false',SCHOLARTRACE_ENABLE_OCR='false',
                      SCHOLARTRACE_ENABLE_TABLES='false',LLM_API_KEY='',SCHOLARTRACE_CONTACT_EMAIL='')
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen,args.output))


if __name__=='__main__':main()
