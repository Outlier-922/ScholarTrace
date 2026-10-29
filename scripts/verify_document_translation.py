"""Frozen full-paper run: at most 55 new requests; stop on the first failed unit."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile
import io

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def exercise(frozen,output):
    from backend.app import document_translation as full, workflow, store, model_client, translation, research_store
    full.repo.init_db();research_store.init_db();workflow.repo.init_db();full.start();workflow.start()
    original=model_client.httpx.AsyncClient;calls=[];job_id=None
    def save(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    class RecordedClient:
        def __init__(self,**kwargs):self.inner=original(**kwargs)
        async def __aenter__(self):await self.inner.__aenter__();return self
        async def __aexit__(self,*args):return await self.inner.__aexit__(*args)
        async def post(self,url,**kwargs):
            if len(calls)>=frozen['external_model_call_cap']:raise RuntimeError('Frozen model cap reached before HTTP')
            unit=next((unit for unit in full.repo.items(job_id,pending=True) if unit['status']=='running'),None)
            record={'number':len(calls)+1,'block_id':unit['block_id'] if unit else None,'page':unit['page'] if unit else None,'request':kwargs['json']}
            calls.append(record);save('calls.json',calls)
            try:
                response=await self.inner.post(url,**kwargs)
                record.update(status_code=response.status_code,response=response.json());save('calls.json',calls);return response
            except Exception as error:
                record['error_code']=type(error).__name__;save('calls.json',calls);raise
    async def batch(job,**options):
        await full.control(job['id'],full.BatchRequest(action='start' if job['status']=='planned' else 'resume',expected_revision=job['revision'],**options))
        worker=full._workers.get(job['id'])
        if worker:await worker
        return full.repo.get(job['id'])
    before=store.stats()
    try:
        assert model_client.identity()==frozen['model_identity'] and model_client.configuration()['ready']
        source=frozen['source'];document=store.get_document(source['document_id'])
        assert document['sha256']==source['sha256'] and document['parse_run_id']==source['parse_run_id']
        structure=store.get_structure(document['id'],parse_run_id=source['parse_run_id'])
        assert hashlib.sha256(json.dumps([{key:block[key] for key in ['id','text','page','bbox']} for block in structure['blocks']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()==source['blocks_sha256']
        book=frozen['longbook'];book_plan=full.build_plan(full.PlanRequest(document_id=book['document_id'],parse_run_id=book['parse_run_id']))
        save('longbook_plan.json',book_plan);assert book_plan['page_count']==541 and book_plan['total']==12003 and book_plan['status']=='planned'
        task=await workflow.plan(workflow.PlanRequest(goal='【全文翻译实测】翻译 DistilBERT 全文，再整理写作框架与任务流程。',document_ids=[document['id']],actions=['translation','writing','diagram'],
            translation={'document':{'document_id':document['id'],'parse_run_id':source['parse_run_id'],'scope':'document'},'glossary':frozen['glossary']},
            use_model=False,max_tool_calls=8,max_model_calls=0,max_metadata_requests=0,max_output_tokens=600,max_active_seconds=180))
        save('research_plan.json',task)
        await workflow.control(task['id'],workflow.Control(action='start',expected_revision=task['revision']))
        if worker:=workflow._workers.get(task['id']):await worker
        paused=workflow.repo.get(task['id']);save('research_waiting.json',paused);assert paused['status']=='paused'
        step=next(step for step in paused['steps'] if step['tool']=='translation');job_id=step['result']['id'];job=full.repo.get(job_id)
        save('paper_plan.json',job)
        assert job['manifest_digest']==frozen['manifest_digest'] and job['estimate']==frozen['estimate']
        offline=await batch(job,allow_model=False,max_model_calls=0,max_output_tokens=0,max_units=1000)
        save('offline_cache_first.json',offline);assert offline['status']=='paused' and offline['totals']['calls']==0
        job=offline;model_client.httpx.AsyncClient=RecordedClient
        for index in range(1,4):
            if job['status'] in {'completed','completed_with_gaps'}:break
            job=await batch(job,allow_model=True,max_model_calls=20,max_output_tokens=70000,max_units=20,max_active_seconds=900)
            save(f'paper_batch_{index}.json',job)
            summary={'application_version':frozen['application_version'],'client_date':frozen['client_date'],'task_id':task['id'],'translation_id':job_id,'status':job['status'],'counts':job['counts'],'totals':job['totals'],'actual_new_requests':len(calls),'semantic_quality_verified':False}
            save('summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
            if job['status'] not in {'budget_exhausted','completed','completed_with_gaps'}:
                (output/'partial_translation.zip').write_bytes(full.export(job_id).body)
                print('Stopped at first failure/interruption; no automatic retry.',flush=True);return
        assert job['status']=='completed' and job['counts'].get('pending',0)==0 and len(calls)<=frozen['external_model_call_cap']
        save('paper_complete.json',full.detail(job_id));(output/'paper_complete.zip').write_bytes(full.export(job_id).body)
        # A second full-range plan is deliberately cache-only. No new API calls.
        replay=full.build_plan(full.PlanRequest(document_id=document['id'],parse_run_id=source['parse_run_id'],glossary=frozen['glossary']))
        replay=await batch(replay,allow_model=False,max_model_calls=0,max_output_tokens=0,max_units=1000)
        save('cache_replay.json',replay);assert replay['status']=='completed' and replay['totals']['calls']==0
        await workflow.control(task['id'],workflow.Control(action='resume',expected_revision=paused['revision']))
        if worker:=workflow._workers.get(task['id']):await worker
        final=workflow.repo.get(task['id']);save('research_complete.json',final)
        assert final['status']=='completed_with_gaps' and final['sources']==task['sources'] and final['usage']['model']['calls']==0
        (output/'research_complete.zip').write_bytes(workflow.export(task['id']).body)
        for name in ['paper_complete.zip','research_complete.zip']:
            with zipfile.ZipFile(output/name) as archive:assert archive.testzip() is None
        assert store.stats()==before
        summary.update(status=job['status'],research_status=final['status'],cache_replay_id=replay['id'],cache_replay_calls=0,library_stats=before,semantic_quality_verified=False)
        save('summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    finally:
        model_client.httpx.AsyncClient=original
        await full.shutdown();await workflow.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['freeze','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Preserve first outputs; choose a new directory.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'));assert frozen['external_model_call_cap']<=55
    for name,digest in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()),SCHOLARTRACE_ENABLE_DENSE='false',SCHOLARTRACE_ENABLE_OCR='false',SCHOLARTRACE_ENABLE_TABLES='false',SCHOLARTRACE_CONTACT_EMAIL='')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen,args.output))


if __name__=='__main__':main()
