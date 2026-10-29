"""Frozen DOI discovery with at most one registry request and zero model calls."""
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
    from backend.app import workflow,literature,literature_store,store
    original=literature._fetch;requests=[];cases=[]
    def save(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    async def recorded(provider,params=None,doi=None):
        if len(requests)>=frozen['registry_request_cap']:raise RuntimeError('Frozen registry request cap reached; no request sent.')
        record={'provider':provider,'params':params,'doi':doi};requests.append(record);save('registry_records.json',requests)
        try:
            body=await original(provider,params=params,doi=doi)
            record['body_utf8']=body.decode('utf-8');save('registry_records.json',requests)
            return body
        except Exception as error:
            record['error_code']=type(error).__name__;save('registry_records.json',requests);raise
    literature_store.init_db();workflow.repo.init_db();workflow.start();before=store.stats()
    try:
        source=frozen['source'];document=store.get_document(source['document_id']);assert document['sha256']==source['sha256']
        structure=store.get_structure(source['document_id'],parse_run_id=source['parse_run_id'])
        assert hashlib.sha256(json.dumps([{key:b[key] for key in ['id','text','page','bbox']} for b in structure['blocks']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()==source['blocks_sha256']
        literature._fetch=recorded
        for name,network,actions,goal in [
            ('offline_before','cache_only',['discovery'],'【离线书目预览】查找指定BERT论文的书目'),
            ('registry_live','allow',['discovery'],'【真实书目发现】查找指定BERT论文，核对作者与年份'),
            ('cached_chain','cache_only',['discovery','writing','diagram'],'【书目缓存与写作流程】查找BERT论文书目，组织原文写作框架和任务流程图'),
        ]:
            body=workflow.PlanRequest(goal=goal,document_ids=[source['document_id']],actions=actions,
                                      discovery={**frozen['discovery_spec'],'network':network},use_model=False,
                                      max_tool_calls=8,max_metadata_requests=1,max_model_calls=0,max_output_tokens=600,max_active_seconds=180)
            task=await workflow.plan(body);workflow.repo.mutate(task['id'],lambda value:value.update(status='queued'),task['revision'])
            await workflow.run(task['id']);result=workflow.repo.get(task['id']);save(name+'.json',result)
            assert result['status']=='completed_with_gaps' and result['usage']['model']['calls']==0
            assert result['sources']==task['sources'] and result['budgets']==task['budgets']
            for step in result['steps']:
                if step.get('result'):workflow.validate_result(result,step['result'])
            (output/(name+'.zip')).write_bytes(workflow.export(task['id']).body)
            with zipfile.ZipFile(output/(name+'.zip')) as archive:
                assert archive.testzip() is None and 'discovery/discovery-crossref.json' in archive.namelist()
                assert json.loads(archive.read('task.json'))==result
            discovery_steps=[step for step in result['steps'] if step['tool']=='discovery']
            data=next((step['result'] for step in reversed(discovery_steps) if step.get('result')),None)
            assert data and data['fulltext_imported'] is False
            saved=literature_store.get_search(data['search_id']);assert saved['result']['items']==data['items']
            cases.append({'name':name,'task_id':task['id'],'status':result['status'],'provider_status':data['provider_status'],
                          'candidate_count':len(data['items']),'excluded_count':len(data['excluded_candidates']),
                          'usage':result['usage'],'search_id':data['search_id'],'zip_valid':True})
            save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,
                                 'registry_attempts':len(requests),'external_model_calls':0,'semantic_quality_verified':False})
            print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
            if network=='cache_only':assert result['usage']['metadata']['requests']==0
        assert store.stats()==before
        save('summary.json',{'application_version':frozen['application_version'],'client_date':frozen['client_date'],'cases':cases,
                             'registry_attempts':len(requests),'external_model_calls':0,'library_stats':before,'semantic_quality_verified':False})
    finally:
        literature._fetch=original
        await workflow.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['freeze','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Preserve first outputs; choose a new directory.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'));assert frozen['registry_request_cap']==1 and frozen['external_model_call_cap']==0
    for name,expected in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==expected,name
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()),SCHOLARTRACE_ENABLE_DENSE='false',SCHOLARTRACE_ENABLE_OCR='false',
                      SCHOLARTRACE_ENABLE_TABLES='false',LLM_API_KEY='',SCHOLARTRACE_CONTACT_EMAIL='')
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen,args.output))


if __name__=='__main__':main()
