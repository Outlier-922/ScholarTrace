"""Replay saved claims offline; optional frozen two-question live run, no retries."""
import argparse,asyncio,hashlib,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

async def run(frozen,out,live):
    from backend.app import qa,store,model_client
    pack=json.loads((ROOT/'evaluation/qa_stage11_regression.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((ROOT/'evaluation/qa_stage11_regression.json').read_text(encoding='utf-8').encode()).hexdigest()==frozen['pack_sha256']
    source=frozen['source'];doc=store.get_internal_document(source['document_id'])
    assert doc['sha256']==source['sha256'] and hashlib.sha256(Path(doc['file_path']).read_bytes()).hexdigest()==source['sha256']
    structure=store.get_structure(doc['id'],parse_run_id=source['parse_run_id'])
    assert hashlib.sha256(json.dumps(structure['blocks'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()==source['blocks_sha256']
    old=json.loads((ROOT/'data/stage11_live_v1/20261028T105135Z-d1bb35de/raw.json').read_text(encoding='utf-8'));replays=[]
    for row in old:
        response=row['response'];detail={};kept=qa._validate_claims({'claims':response['claims']},response['evidence'],detail)
        replays.append({'case_id':row['case_id'],'scope':'Replay of previously retained claims only; original rejected model rows were not saved',
                        'before_claims':response['claims'],'after_claims':kept,'validation':detail,'actual_model_calls':0})
    save(out/'saved_claim_replay.json',replays)
    if not live:return
    assert frozen['max_model_calls']==2 and model_client.identity()==frozen['model_identity'] and model_client.configuration()['ready']
    original=model_client.httpx.AsyncClient;calls=[];results=[];budget=model_client.CallBudget(2,3200)
    class RecordedClient:
        def __init__(self,**kwargs):self.inner=original(**kwargs)
        async def __aenter__(self):await self.inner.__aenter__();return self
        async def __aexit__(self,*args):return await self.inner.__aexit__(*args)
        async def post(self,url,**kwargs):
            assert len(calls)<2
            entry={'number':len(calls)+1,'request':kwargs['json']};calls.append(entry);save(out/'calls.json',calls)
            try:
                response=await self.inner.post(url,**kwargs);entry.update(status_code=response.status_code,response=response.json());save(out/'calls.json',calls);return response
            except Exception as error:
                entry['error_type']=type(error).__name__;save(out/'calls.json',calls);raise
    before=store.stats();model_client.httpx.AsyncClient=RecordedClient
    try:
        with model_client.metered(budget):
            for identity in frozen['case_ids']:
                case=next(x for x in pack['cases'] if x['id']==identity)
                request=qa.AskRequest(question=case['question'],document_ids=[doc['id']],parse_runs={doc['id']:source['parse_run_id']},limit=6,use_model=True)
                response=await qa.ask(request);save(out/(identity+'.json'),response)
                results.append({'case_id':identity,'run_id':response['run_id'],'mode':response['mode'],'claims':response['claims'],
                                'coverage':response['answer_coverage'],'validation':response.get('field_validation'),'semantic_verified':False})
                save(out/'summary.json',{'cases':results,'usage':budget.snapshot(),'actual_model_calls':len(calls),'scientific_quality_verified':False})
                if response['mode']!='llm' or any(step['status']=='failed' for step in response['steps']):break
        assert store.stats()==before
    finally:model_client.httpx.AsyncClient=original

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,digest in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    assert not args.output.exists(),'Preserve first output; choose a fresh directory.'
    os.environ['SCHOLARTRACE_ENABLE_DENSE']='false';os.environ['SCHOLARTRACE_DATA_DIR']=str(ROOT/'data')
    if args.live:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env',override=False)
    args.output.mkdir(parents=True);asyncio.run(run(frozen,args.output,args.live))

if __name__=='__main__':main()
