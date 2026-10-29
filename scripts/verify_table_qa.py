"""Frozen table-QA smoke; optional paid generation is capped and recorded."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--freeze',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--live-model',action='store_true');args=p.parse_args()
    if args.output.exists():p.error('Preserve first results; select a new output path.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,sha in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()!=sha:raise SystemExit('Frozen code changed: '+name)
    os.environ['SCHOLARTRACE_DATA_DIR']=str(args.workspace.resolve())
    for feature in ['OCR','TABLES','DENSE']:os.environ['SCHOLARTRACE_ENABLE_'+feature]='false'
    os.environ['SCHOLARTRACE_DEMO_MODE']='false'
    from backend.app import model_client,table_qa,table_refine_store as repo
    from backend.app.main import app
    from fastapi.testclient import TestClient
    for item in frozen['source_snapshots']:
        if table_qa.digest(repo.get(item['source_id']))!=item['source_digest']:raise SystemExit('Frozen table changed.')
        if item['revision_id'] and table_qa.digest(repo.get(item['revision_id']))!=item['revision_digest']:raise SystemExit('Frozen revision changed.')
    args.output.mkdir(parents=True)
    def save(name,value):(args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    save('manifest.json',{'started_at':datetime.now(timezone.utc).isoformat(),'live_model':args.live_model,'freeze':str(args.freeze),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    budget=model_client.CallBudget(max_calls=min(2,frozen['max_model_calls']),max_output_tokens=min(3200,frozen['max_reserved_output_tokens']))
    original=model_client.chat_json;requests=[];responses=[];results=[]
    async def capture(system,payload,*,max_tokens):
        if not args.live_model:raise ValueError('Provider access disabled; use --live-model explicitly')
        assert 'table_drafts' not in payload
        requests.append({'system':system,'payload':payload,'max_tokens':max_tokens});save('provider_requests.json',requests)
        with model_client.metered(budget):response=await original(system,payload,max_tokens=max_tokens)
        responses.append(response);save('provider_first_responses.json',responses)
        return response
    model_client.chat_json=capture
    try:
        with TestClient(app) as client:
            for case in frozen['cases']:
                before=budget.calls;request={**case['request'],'use_model':args.live_model}
                r=client.post('/api/ask',json=request);r.raise_for_status();result=r.json()
                actual={(c['table_cell']['row'],c['table_cell']['column'],c['table_cell']['value']) for c in result['claims'] if c.get('table_cell')}
                expected={tuple(v) for v in case['expected_native_cells']}
                results.append({'case_id':case['id'],'request':request,'provider_calls':budget.calls-before,'expected_native_cells_present':sorted(expected&actual),'expected_native_cells_missing':sorted(expected-actual),'unexpected_claim_cells':sorted(actual-expected),'result':result})
                assert all(table_qa.valid_evidence(e) for e in result['evidence'])
                assert all(d['table_draft']['sent_to_model'] is False for d in result['table_drafts'])
                if not case['expected_provider_calls']:assert budget.calls==before and not result['claims'] and not result['evidence']
                historical=client.get('/api/agent-runs/'+result['run_id']);historical.raise_for_status()
                assert historical.json()['table_context']==result['table_context']
                save('results.json',{'budget':budget.snapshot(),'items':results,'semantic_verified':False})
                print(json.dumps({'case':case['id'],'provider_calls':budget.calls-before,'claims':len(result['claims']),'drafts':len(result['table_drafts']),'expected_cells_missing':len(expected-actual)}))
    finally:model_client.chat_json=original


if __name__=='__main__':main()
