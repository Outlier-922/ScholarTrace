"""Replay an existing frozen native-formula smoke test; paid access is explicit."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--freeze',type=Path,required=True)
    p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--live-model',action='store_true')
    args=p.parse_args()
    if args.output.exists():raise SystemExit('Preserve prior results; select a new output path.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,sha in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()!=sha:raise SystemExit('Frozen product changed: '+name)
    if not 1<=len(frozen['sources'])<=2:raise SystemExit('Select one or two preselected sources.')
    os.environ['SCHOLARTRACE_DATA_DIR']=str(args.workspace.resolve())
    for feature in ['OCR','TABLES','DENSE']:os.environ['SCHOLARTRACE_ENABLE_'+feature]='false'
    os.environ['SCHOLARTRACE_DEMO_MODE']='false'
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import model_client
    budget=model_client.CallBudget(max_calls=2,max_output_tokens=3200)
    original=model_client.chat_json
    observed=[];responses=[]
    manifest={'started_at':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'freeze':str(args.freeze),'live_model_requested':args.live_model,'sources':frozen['sources']}
    args.output.mkdir(parents=True)
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    async def capture(system,payload,*,max_tokens):
        if not args.live_model:raise ValueError('Live model access requires --live-model')
        with model_client.metered(budget):
            value=await original(system,payload,max_tokens=max_tokens)
        observed.append(value)
        (args.output/'provider_first_responses.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2),encoding='utf-8')
        return value
    model_client.chat_json=capture
    try:
        with TestClient(app) as client:
            for source in frozen['sources']:
                url='/api/formula-sources/'+source['id']
                actual=client.get(url);actual.raise_for_status();value=actual.json()
                for key in ['sha256','parse_run_id','page','image_sha256']:
                    if value[key]!=source[key]:raise ValueError('Frozen source changed: '+key)
                if hashlib.sha256(json.dumps(value['glyphs'],sort_keys=True).encode()).hexdigest()!=source['glyphs_sha256']:raise ValueError('Frozen glyphs changed')
                response=client.post(url+'/transcribe',json={})
                responses.append({'source_id':source['id'],'page':source['page'],'http_status':response.status_code,'response':response.json()})
                (args.output/'endpoint_first_responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2),encoding='utf-8')
                if response.is_success:
                    count=len(observed);cached=client.post(url+'/transcribe',json={})
                    responses[-1]['cache_reused']=cached.is_success and cached.json()['id']==response.json()['id'] and len(observed)==count
            result={'completed_at':datetime.now(timezone.utc).isoformat(),'model':{k:v for k,v in model_client.configuration().items() if k in {'profile','endpoint_host','model'}},'budget':budget.snapshot(),'provider_responses':observed,'items':responses,'semantic_verified':False,'scope':'Two previously observed-paper native formulas; not independent accuracy or scan-formula acceptance.'}
            (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'requests':budget.calls,'statuses':[r['http_status'] for r in responses],'cached':[r.get('cache_reused') for r in responses]},ensure_ascii=False))
    finally:model_client.chat_json=original


if __name__=='__main__':main()
