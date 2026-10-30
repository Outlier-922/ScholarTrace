"""One frozen official development question, at most one model request."""
import argparse,asyncio,hashlib,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

async def run(frozen,out):
    from backend.app import qa,store,model_client
    source=frozen['source'];document=store.get_internal_document(source['document_id'])
    assert document['sha256']==source['sha256'] and hashlib.sha256(Path(document['file_path']).read_bytes()).hexdigest()==source['sha256']
    structure=store.get_structure(document['id'],parse_run_id=source['parse_run_id'])
    assert hashlib.sha256(json.dumps(structure['blocks'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()==source['blocks_sha256']
    assert model_client.identity()==frozen['model_identity'] and model_client.configuration()['ready']
    original=model_client.httpx.AsyncClient;calls=[];budget=model_client.CallBudget(1,1600)
    class RecordedClient:
        def __init__(self,**kwargs):self.inner=original(**kwargs)
        async def __aenter__(self):await self.inner.__aenter__();return self
        async def __aexit__(self,*args):return await self.inner.__aexit__(*args)
        async def post(self,url,**kwargs):
            assert not calls,'One-request frozen cap; no retry'
            entry={'number':1,'request':kwargs['json']};calls.append(entry);save(out/'calls.json',calls)
            try:
                response=await self.inner.post(url,**kwargs);entry.update(status_code=response.status_code,response=response.json());save(out/'calls.json',calls);return response
            except Exception as error:
                entry['error_type']=type(error).__name__;save(out/'calls.json',calls);raise
    model_client.httpx.AsyncClient=RecordedClient
    try:
        with model_client.metered(budget):
            result=await qa.ask(qa.AskRequest(question=frozen['question'],document_ids=[document['id']],parse_runs={document['id']:source['parse_run_id']},limit=6,use_model=True))
        save(out/'answer.json',result);save(out/'summary.json',{'application_version':'0.24.2','case_id':'H01','run_id':result['run_id'],'mode':result['mode'],'claims':result['claims'],'actual_model_calls':len(calls),'usage':budget.snapshot(),'scientific_quality_verified':False})
    finally:model_client.httpx.AsyncClient=original

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--live',action='store_true',required=True);args=parser.parse_args()
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'));assert frozen['max_model_calls']==1 and frozen.get('additional_model_call_cap',1)==1
    for name,digest in frozen['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    assert not args.output.exists(),'Do not overwrite first output'
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False);os.environ['SCHOLARTRACE_DATA_DIR']=str(ROOT/'data')
    args.output.mkdir(parents=True);asyncio.run(run(frozen,args.output))

if __name__=='__main__':main()
