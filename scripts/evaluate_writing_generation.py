"""Run one frozen real writing request; preserve its first result, including rejection."""
import argparse,asyncio,hashlib,io,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


async def run(f,out):
    import httpx
    from backend.app import model_client,studio,studio_store as repo,store,writing_generation as writing
    assert model_client.identity()==f['model_identity']
    draft=repo.get(f['draft_id']);assert draft['revision']==1
    assert hashlib.sha256(json.dumps(draft,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==f['draft_sha256']
    assert writing.context(draft)==f['context']
    before=store.stats();calls=[];original=httpx.AsyncClient.send
    async def send(client,request,*args,**kwargs):
        assert not calls and request.url.host==f['endpoint_host'] and request.url.scheme=='https'
        payload=json.loads(request.content);assert payload['max_tokens']==f['max_output_tokens']==2400
        call={'request':payload};calls.append(call);save(out/'calls.json',calls)
        response=await original(client,request,*args,**kwargs)
        call.update(status_code=response.status_code,response=response.json());save(out/'calls.json',calls)
        return response
    httpx.AsyncClient.send=send
    try:
        attempt=await writing.generate(draft,writing.GenerateRequest(**f['request']))
        save(out/'attempt.json',attempt)
        after=repo.get(draft['id']);save(out/'draft_after.json',after)
        assert repo.get(draft['id'],1)==draft and before==store.stats()
        assert after['references']==draft['references'] and after['sources']==draft['sources'] and after['evidence']==draft['evidence']
        bundle=studio.export_draft(after['id'],format='bundle',revision=after['revision']).body
        (out/'writing_evidence.zip').write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:assert archive.testzip() is None
        assert len(calls)==1 and attempt['usage']['calls']==1
        facts=[p for s in after['sections'] for p in s['paragraphs'] if p['kind']=='fact']
        summary={'application_version':f['application_version'],'client_date':f['client_date'],'draft_id':draft['id'],'attempt_id':attempt['id'],'status':attempt['status'],
                 'error':attempt['error'],'initial_revision':1,'final_revision':after['revision'],'actual_model_calls':len(calls),'usage':attempt['usage'],
                 'fact_paragraphs':len(facts),'source_quotes':sum(bool(p.get('support_quote')) for p in facts),'original_revision_preserved':True,
                 'sources_and_references_preserved':True,'scientific_quality_verified':False}
        save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))
    finally:httpx.AsyncClient.send=original


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    f=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,digest in f['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    assert not args.output.exists(),'Preserve first result';args.output.mkdir(parents=True)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env');asyncio.run(run(f,args.output))


if __name__=='__main__':main()
