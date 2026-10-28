"""Small frozen-scope probes. Empty retained claims are not adjudicated correctness."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlparse
import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.eval_core import bind_sources,digest,request_body,score_response
from scripts.evaluate_qa import fingerprint


def disposition(response):
    generated=response.get('mode')=='llm'
    count=len(response.get('claims',[]))
    return {'generated':generated,'retained_claims':count,'no_retained_factual_claims':not count if generated else None,
            'empty_result_origin':'unknown_model_or_validation_filter' if generated and not count else None,
            'semantic_correctness':None,'review_status':'pending_independent_review'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--pack',type=Path,default=Path('evaluation/qa_holdout_v3.json'))
    parser.add_argument('--freeze',type=Path,default=Path('evaluation/freeze_holdout_v3.json'))
    parser.add_argument('--output',type=Path,default=Path('data/stage10_missing_fields'))
    parser.add_argument('--live-model',action='store_true')
    args=parser.parse_args()
    if not args.live_model:parser.error('Requires explicit --live-model; at most two generation requests.')
    if urlparse(args.api).hostname not in {'localhost','127.0.0.1','::1'}:parser.error('Loopback API only.')
    pack=json.loads(args.pack.read_text(encoding='utf-8'));frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    if any(frozen.get(key)!=value for key,value in fingerprint(pack).items()):raise SystemExit('Frozen product/pack changed.')
    cases=pack['negative_checks'];assert 0<len(cases)<=2
    root=args.output/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    rows=[]
    with httpx.Client(base_url=args.api,timeout=180,trust_env=False) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response.json()
        bound=bind_sources(pack,get('/documents')['items'],lambda identity,run:get('/documents/'+identity+'/structure',params={'parse_run_id':run}))
        if not get('/model/status').get('ready'):raise SystemExit('Provider not configured; no generation requests sent.')
        for case in cases:
            row={'case_id':case['id'],'case_sha256':digest(case),'question':case['question'],
                 'source_runs':{key:bound[key]['parse_run_id'] for key in case['sources']}}
            started=time.perf_counter()
            try:
                result=client.post('/ask',json=request_body(case,bound,True));result.raise_for_status();response=result.json()
                row.update(response_sha256=digest(response),response=response,**disposition(response),
                           anchor_check=score_response({**case,'gold_groups':[]},response,bound))
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as error:row['error_type']=type(error).__name__
            row['elapsed_seconds']=round(time.perf_counter()-started,4);rows.append(row)
            print(case['id']+': '+json.dumps({key:value for key,value in row.items() if key not in {'response','question','anchor_check'}},ensure_ascii=False),flush=True)
    report={'pack_sha256':digest(pack),'product_files':frozen['files'],
            'fingerprint_format':frozen.get('fingerprint_format','raw_bytes_legacy'),'max_generation_requests':2,
            'max_reserved_output_tokens':3200,'quality_notice':'Assistant-authored absence probes; text search plus hardware-section review is not independent adjudication. No correctness or abstention accuracy is computed.',
            'cases':[{key:value for key,value in row.items() if key!='response'} for row in rows]}
    (root/'raw.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved:',root)


if __name__=='__main__':main()
