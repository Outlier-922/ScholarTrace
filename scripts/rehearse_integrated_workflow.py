"""Zero-external-request rehearsal on existing official sources and real caches."""
import argparse,asyncio,hashlib,io,json,os,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def verify_reproduction(f,out,*,retry=False):
    from backend.app import store
    final=json.loads((out/'task.json').read_text(encoding='utf-8'))
    results={s['tool']:s['result'] for s in final['steps']}
    for kind in ['plot','diagram']:
        folder=(out/'reproduction'/kind).resolve()
        reproduced=subprocess.run([sys.executable,str(folder/'render.py')],cwd=folder,capture_output=True,text=True,encoding='utf-8',timeout=60)
        suffix='_reproduction_retry_log.txt' if retry else '_reproduction_log.txt'
        (out/(kind+suffix)).write_text(reproduced.stdout+reproduced.stderr,encoding='utf-8',newline='\n')
        assert reproduced.returncode==0
        if kind=='plot':
            for name in ['processed.csv','caption.txt']:assert (folder/name).read_bytes()==(folder/'reproduced'/name).read_bytes()
    save(out/'summary.json',{'application_version':f['application_version'],'client_date':f['client_date'],'task_id':final['id'],'status':final['status'],'steps':len(final['steps']),
        'budget_stop_after':2,'previous_steps_reused':True,'usage':final['usage'],'source_count':len(final['sources']),'metadata_cache':results['discovery']['provider_status']['cache'],
        'translation_status':results['translation']['translation_status'],'dataset_status':'synthetic_demo','plot_reproduced':True,'diagram_reproduced':True,
        'graph_nodes':len(results['graph']['nodes']),'graph_edges':len(results['graph']['edges']),'writing_references':len(results['writing']['references']),
        'external_requests':0,'unexpected_request_attempts':[],'library_stats':store.stats(),'scientific_quality_verified':False,'verification_driver_path_corrected':retry})
    print(json.dumps({'task_id':final['id'],'status':final['status'],'steps':len(final['steps']),'external_requests':0,'plot_and_diagram_reproduced':True},ensure_ascii=False))

async def exercise(f,out):
    import httpx
    from backend.app import workflow,store,studio,model_client,literature,workflow_plot,workflow_translation
    original_async=httpx.AsyncClient.send;original_sync=httpx.Client.send
    attempts=[]
    def outbound(request):
        if request.url.host not in {'127.0.0.1','localhost','::1'}:
            attempts.append({'kind':'outbound_http','host':request.url.host});save(out/'unexpected_requests.json',attempts)
            raise AssertionError('Rehearsal forbids external HTTP')
    async def async_send(self,request,*args,**kwargs):outbound(request);return await original_async(self,request,*args,**kwargs)
    def sync_send(self,request,*args,**kwargs):outbound(request);return original_sync(self,request,*args,**kwargs)
    async def forbidden(*args,**kwargs):
        attempts.append({'kind':'unexpected_generation_or_download'});save(out/'unexpected_requests.json',attempts)
        raise AssertionError('Rehearsal forbids generation and download')
    original_model=model_client.chat_json;original_fetch=literature._fetch;original_download=literature.download_pdf
    httpx.AsyncClient.send=async_send;httpx.Client.send=sync_send
    model_client.chat_json=forbidden;literature._fetch=forbidden;literature.download_pdf=forbidden
    workflow.start();workflow.repo.init_db();before=store.stats()
    async def control(task,action,**kwargs):
        await workflow.control(task['id'],workflow.Control(action=action,expected_revision=task['revision'],**kwargs))
        if worker:=workflow._workers.get(task['id']):await worker
        return workflow.repo.get(task['id'])
    try:
        assert f['external_request_cap']==0 and model_client.identity()==f['model_identity']
        for source in f['sources']:
            doc=store.get_internal_document(source['document_id'])
            assert hashlib.sha256(Path(doc['file_path']).read_bytes()).hexdigest()==source['sha256']
            blocks=store.get_structure(doc['id'],parse_run_id=source['parse_run_id'])['blocks']
            assert hashlib.sha256(json.dumps(blocks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==source['blocks_sha256']
        # Real HTTP upload of an already imported official file, explicitly reuse.
        doc=store.get_internal_document(f['translation']['passages'][0]['document_id'])
        with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=30,trust_env=False) as client:
            with Path(doc['file_path']).open('rb') as stream:
                response=client.post('/documents',files={'files':(doc['filename'],stream,'application/pdf')});response.raise_for_status()
            uploaded=response.json();save(out/'duplicate_upload.json',uploaded)
            assert uploaded['items'][0]['id']==doc['id'] and uploaded['items'][0]['upload_outcome']=='reused'
            answer=client.get('/agent-runs/'+f['saved_qa_run_id']);answer.raise_for_status();save(out/'saved_qa.json',answer.json())
        dataset=studio.create_dataset(studio.demo_bytes(),'integrated_rehearsal_SYNTHETIC.csv','synthetic_demo');save(out/'dataset.json',dataset)
        assert dataset['sha256']==f['synthetic_csv_sha256']
        plan=await workflow.plan(workflow.PlanRequest(goal=f['goal'],document_ids=[s['document_id'] for s in f['sources']],parse_runs={s['document_id']:s['parse_run_id'] for s in f['sources']},
            handoff_id=f['handoff_id'],actions=f['actions'],dimensions=['architecture','objective'],translation=f['translation'],discovery=f['discovery'],
            plot={'dataset_id':dataset['id'],'sha256':dataset['sha256'],'spec':f['chart_spec']},use_model=False,max_tool_calls=2,max_model_calls=0,max_metadata_requests=0,max_output_tokens=600,max_active_seconds=900))
        save(out/'plan.json',plan);assert len(plan['steps'])==9
        stopped=await control(plan,'start');save(out/'budget_stop.json',stopped)
        assert stopped['status']=='budget_exhausted' and stopped['usage']['tool_calls']==2
        assert [s['status'] for s in stopped['steps'][:2]]==['completed','completed'] and stopped['steps'][2]['attempts']==0
        (out/'partial_task.zip').write_bytes(workflow.export(plan['id']).body)
        final=await control(stopped,'resume',max_tool_calls=12);save(out/'task.json',final)
        assert final['status']=='completed_with_gaps' and all(s['status']=='completed' for s in final['steps'])
        assert final['sources']==plan['sources'] and final['steps'][:2]==stopped['steps'][:2]
        assert final['usage']['model']['calls']==final['usage']['metadata']['requests']==0 and final['usage']['tool_calls']==9
        results={s['tool']:s['result'] for s in final['steps']}
        for value in results.values():workflow.validate_result(final,value)
        assert results['discovery']['provider_status']['status']=='ok' and len(results['discovery']['items'])==1
        assert results['discovery']['network_mode']=='cache_only' and results['discovery']['fulltext_imported'] is False
        assert results['translation']['translation_status']=='cached'
        assert results['evidence']['mode']==results['review']['mode']=='evidence_only'
        assert results['writing']['mode']=='evidence_plan' and results['plot']['data_status']=='synthetic_demo'
        if f.get('expected_confirmed_references'):
            confirmed=[r for r in results['writing']['references'] if r.get('identity_basis')=='confirmed_fulltext_handoff']
            assert len(confirmed)==f['expected_confirmed_references'] and results['writing']['handoff']==plan['handoff']
            assert confirmed[0]['metadata']==plan['handoff']['metadata']
        assert not attempts
        bundle=workflow.export(plan['id']).body;(out/'research_rehearsal.zip').write_bytes(bundle)
        unpack=out/'reproduction'
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            assert archive.testzip() is None and json.loads(archive.read('task.json'))==final
            assert hashlib.sha256(archive.read('plot/input.csv')).hexdigest()==dataset['sha256']
            for name in archive.namelist():
                assert (unpack/name).resolve().is_relative_to(unpack.resolve())
                if name.startswith(('plot/','diagram/')):archive.extract(name,unpack)
        verify_reproduction(f,out)
        assert store.stats()==before
    finally:
        await workflow.shutdown();httpx.AsyncClient.send=original_async;httpx.Client.send=original_sync
        model_client.chat_json=original_model;literature._fetch=original_fetch;literature.download_pdf=original_download

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    f=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,digest in f['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False);os.environ['SCHOLARTRACE_DATA_DIR']=str(ROOT/'data')
    if args.verify_only:
        assert args.output.is_dir();verify_reproduction(f,args.output,retry=True);return
    assert not args.output.exists(),'Preserve first output'
    args.output.mkdir(parents=True);asyncio.run(exercise(f,args.output))

if __name__=='__main__':main()
