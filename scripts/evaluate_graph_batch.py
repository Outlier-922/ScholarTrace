"""Import a frozen official corpus and preserve a first offline graph snapshot."""
import argparse,hashlib,json,sys,time
from contextlib import ExitStack
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--phase',choices=['ingest','graph'],required=True);args=parser.parse_args()
    f=json.loads(args.freeze.read_text(encoding='utf-8'));out=args.output
    for name,digest in f['files'].items():assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    from backend.app import store
    import httpx
    if args.phase=='ingest':
        assert not out.exists();out.mkdir(parents=True)
        save(out/'library_before.json',{'documents':store.list_documents(),'stats':store.stats()})
        with httpx.Client(base_url='http://127.0.0.1:8000/api',trust_env=False,timeout=180) as client:
            save(out/'health.json',client.get('/health').json())
            with ExitStack() as stack:
                files=[]
                for item in f['corpus']:
                    p=Path(f['corpus_root'])/item['filename'];assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
                    files.append(('files',(p.name,stack.enter_context(p.open('rb')),'application/pdf')))
                response=client.post('/documents',files=files);response.raise_for_status();uploaded=response.json();save(out/'uploads.json',uploaded)
            assert len(uploaded['items'])==20 and not uploaded['errors']
            deadline=time.monotonic()+1800;last=None
            while time.monotonic()<deadline:
                documents=[client.get('/documents/'+d['id']).json() for d in uploaded['items']]
                status=[(d['filename'],d['status']) for d in documents]
                if status!=last:print(json.dumps(status),flush=True);last=status;save(out/'parse_progress.json',documents)
                if any(d['status']=='failed' for d in documents):raise AssertionError('Parse failure preserved; do not replace corpus')
                if all(d['status'] in {'ready','partial'} for d in documents):break
                time.sleep(2)
            else:raise TimeoutError('Parse window exceeded; checkpoints preserved')
        sources=[]
        for d in documents:
            structure=store.get_structure(d['id'],parse_run_id=d['parse_run_id'])
            assert len(structure['pages'])==d['page_count']
            sources.append({'document_id':d['id'],'parse_run_id':d['parse_run_id'],'filename':d['filename'],'title':d['title'],'arxiv_id':d.get('arxiv_id'),'page_count':d['page_count'],'status':d['status'],'sha256':store.get_internal_document(d['id'])['sha256'],
                            'block_count':len(structure['blocks']),'references':len(structure['references']),'blocks_sha256':hashlib.sha256(json.dumps(structure['blocks'],ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'parse_config':store.get_parse_run(d['id'],d['parse_run_id'])['config']})
        save(out/'sources.json',sources);save(out/'library_after.json',{'documents':store.list_documents(),'stats':store.stats()})
        print(json.dumps({'sources':len(sources),'pages':sum(s['page_count'] for s in sources),'ready':sum(s['status']=='ready' for s in sources),'partial':sum(s['status']=='partial' for s in sources)},ensure_ascii=False),flush=True)
    else:
        sources=json.loads((out/'sources.json').read_text(encoding='utf-8'))
        assert len(sources)==20 and not (out/'graph.json').exists()
        for s in sources:
            blocks=store.get_structure(s['document_id'],parse_run_id=s['parse_run_id'])['blocks']
            assert hashlib.sha256(json.dumps(blocks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==s['blocks_sha256']
        body={'document_ids':[s['document_id'] for s in sources],'parse_runs':{s['document_id']:s['parse_run_id'] for s in sources},'topic':f['topic'],'resolve_metadata':False,'max_nodes':150}
        save(out/'graph_request.json',body)
        with httpx.Client(base_url='http://127.0.0.1:8000/api',trust_env=False,timeout=180) as client:
            started=time.monotonic();response=client.post('/graphs',json=body);(out/'graph_response.txt').write_text(response.text,encoding='utf-8');response.raise_for_status();result=response.json();save(out/'graph.json',result)
            exported=client.get('/graphs/'+result['id']+'/export?format=json');exported.raise_for_status();view=exported.json();assert view.pop('newer_source_runs',[])==[] and view==result
            save(out/'summary.json',{'application_version':f['application_version'],'source_count':len(result['sources']),'nodes':len(result['nodes']),'edges':len(result['edges']),'metrics':result['metrics'],'unresolved_reference_groups':len(result['unresolved_references']),'elapsed_seconds':round(time.monotonic()-started,3),'model_requests':0,'metadata_requests':0})
            print(json.dumps({'graph_id':result['id'],'sources':len(result['sources']),'nodes':len(result['nodes']),'edges':len(result['edges']),'eligible_edges':result['metrics']['eligible_edges'],'model_requests':0},ensure_ascii=False))


if __name__=='__main__':main()
