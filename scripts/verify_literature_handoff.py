"""Confirm an existing official local PDF from a saved registry snapshot, offline."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def exercise(frozen,output):
    from backend.app import literature, literature_handoff as handoff, workflow, model_client, store
    def forbidden(*args,**kwargs):raise AssertionError('Frozen exercise forbids external requests')
    literature._fetch=forbidden;literature.download_pdf=forbidden;model_client.chat_json=forbidden
    def save(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    literature.repo.init_db();workflow.repo.init_db();workflow.start()
    parent=workflow.repo.get(frozen['context']['parent_task_id']);assert digest(parent)==frozen['parent_digest']
    before=store.stats();source=frozen['source'];document=store.get_document(source['document_id'])
    assert document['sha256']==source['sha256'] and document['parse_run_id']==source['parse_run_id']
    structure=store.get_structure(document['id'],parse_run_id=source['parse_run_id'])
    assert digest([{k:block[k] for k in ['id','text','page','bbox']} for block in structure['blocks']])==source['blocks_sha256']
    try:
        context=handoff.Context(**frozen['context']);item=handoff.metadata(context)
        choices=handoff.candidates(item);save('local_candidates.json',{'items':choices})
        assert document['id'] in {value['id'] for value in choices}
        receipt=handoff.confirm_local(handoff.LocalRequest(**frozen['context'],document_id=document['id'],
                 parse_run_id=source['parse_run_id'],sha256=source['sha256'],confirm_identity=True))
        save('receipt.json',receipt);assert receipt['can_plan'] and receipt['origin']=='confirmed_local'
        assert handoff.ready(receipt['id'])['source']==receipt['source']
        cases=[]
        for name,actions in [('evidence',['evidence']),('writing_chain',['writing','diagram'])]:
            plan=await workflow.plan(workflow.PlanRequest(goal='【全文交接验证】整理已核对BERT的研究方法与局限，保留原文证据。',
                document_ids=[document['id']],handoff_id=receipt['id'],parse_runs={document['id']:source['parse_run_id']},
                actions=actions,use_model=False,max_tool_calls=8,max_model_calls=0,max_metadata_requests=0,max_output_tokens=600,max_active_seconds=180))
            save(name+'_plan.json',plan)
            await workflow.control(plan['id'],workflow.Control(action='start',expected_revision=plan['revision']))
            worker=workflow._workers.get(plan['id'])
            if worker:await worker
            result=workflow.repo.get(plan['id']);save(name+'.json',result)
            assert result['status']=='completed_with_gaps' and result['sources']==plan['sources']
            assert result['sources'][0]['parse_run_id']==source['parse_run_id']
            assert result['usage']['model']['calls']==result['usage']['metadata']['requests']==0
            bundle=workflow.export(plan['id']).body;(output/(name+'.zip')).write_bytes(bundle)
            import io
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                assert archive.testzip() is None
                assert json.loads(archive.read('handoff.json'))==plan['handoff']
                assert json.loads(archive.read('task.json'))==result
            assert digest(workflow.repo.get(parent['id']))==frozen['parent_digest']
            cases.append({'name':name,'task_id':result['id'],'status':result['status'],'usage':result['usage'],'handoff_id':receipt['id'],'parent_unchanged':True,'zip_valid':True})
            save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,
                'external_requests':0,'actual_downloads':0,'mode':'confirmed_existing_local_pdf','semantic_quality_verified':False})
            print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
        assert store.stats()==before
        save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,
            'external_requests':0,'actual_downloads':0,'mode':'confirmed_existing_local_pdf','library_stats':before,'semantic_quality_verified':False})
    finally:await workflow.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['freeze','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Preserve first outputs; choose a new directory.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'));assert frozen['external_request_cap']==0
    for name,digest in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()),SCHOLARTRACE_ENABLE_DENSE='false',SCHOLARTRACE_ENABLE_OCR='false',
                      SCHOLARTRACE_ENABLE_TABLES='false',LLM_API_KEY='',SCHOLARTRACE_CONTACT_EMAIL='')
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen,args.output))


if __name__=='__main__':main()
