"""Freeze and evaluate a draft QA pack. Default: zero generation requests."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_core import bind_sources, digest, request_body, score_response, summarize, validate_pack

PRODUCT_FILES = ['backend/app/qa.py', 'backend/app/model_client.py', 'backend/app/semantic.py',
                 'backend/app/source_scope.py','backend/app/parser.py','backend/app/store.py',
                 'backend/app/comparison.py','backend/app/comparison_quotes.py','backend/app/semantic_jobs.py','backend/app/answer_fields.py',
                 'backend/app/workflow.py','backend/app/workflow_policy.py','backend/app/demo_access.py','backend/app/main.py','backend/app/selection_translation.py',
                 'backend/app/table_qa.py','backend/app/table_refinement.py','backend/app/table_refine_native.py','backend/app/table_refine_store.py','backend/app/formulas.py','backend/app/formula_native.py','backend/app/formula_store.py','backend/app/evidence_repairs.py','backend/app/claim_checks.py','backend/app/insights.py','backend/app/insight_store.py','backend/app/graph.py','backend/app/graph_store.py',
                 'scripts/eval_core.py', 'scripts/evaluate_qa.py']


def fingerprint(pack):
    return {'fingerprint_format':'utf8_lf_text_v1','pack_sha256':digest(pack), 'files':{name:__import__('hashlib').sha256((ROOT/name).read_text(encoding='utf-8').encode('utf-8')).hexdigest()
                                             for name in PRODUCT_FILES}}


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--pack',type=Path,default=Path('evaluation/qa_v1.json'))
    parser.add_argument('--freeze',type=Path,default=Path('evaluation/freeze_v1.json'))
    parser.add_argument('--create-freeze',action='store_true',help='Create an immutable first-run fingerprint and exit')
    parser.add_argument('--split',choices=['all','development','holdout'],default='all')
    parser.add_argument('--cases',nargs='+',help='Explicit case IDs; order follows pack')
    parser.add_argument('--live-model',action='store_true',help='Explicit paid model requests; maximum 8 questions per run')
    parser.add_argument('--output',type=Path,default=Path('data/stage8_evaluation'))
    args=parser.parse_args()
    if urlparse(args.api).hostname not in {'127.0.0.1','localhost','::1'}:
        parser.error('Evaluation runner only supports a loopback API.')
    pack=load_json(args.pack);validate_pack(pack);current=fingerprint(pack)
    if args.create_freeze:
        args.freeze.parent.mkdir(parents=True,exist_ok=True)
        with args.freeze.open('x',encoding='utf-8') as stream:
            json.dump({**current,'created_at':datetime.now(timezone.utc).isoformat(),
                       'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                       'notice':'File hashes fix this local evaluator/product snapshot; not an external attestation.'},stream,ensure_ascii=False,indent=2)
        print('Frozen; no /ask requests sent.');return
    frozen=load_json(args.freeze)
    if any(frozen.get(key)!=value for key,value in current.items()):
        raise SystemExit('Frozen pack/product/evaluator changed. Create a new version; do not overwrite the old freeze.')
    chosen=[case for case in pack['cases'] if args.split=='all' or case['split']==args.split]
    if args.cases:
        if len(set(args.cases))!=len(args.cases) or not set(args.cases)<={case['id'] for case in chosen}:
            parser.error('Unknown, repeated, or out-of-split case IDs.')
        chosen=[case for case in chosen if case['id'] in args.cases]
    if not chosen or (args.live_model and len(chosen)>8):
        parser.error('Select 1-8 cases for --live-model; retrieval-only can run the full pack.')
    args.output.mkdir(parents=True,exist_ok=True)
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    target=args.output/run_id;target.mkdir()
    rows=[]
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=180,trust_env=False) as client:
        def get(path,**kwargs):
            result=client.get(path,**kwargs);result.raise_for_status();return result.json()
        documents=get('/documents')['items']
        bound=bind_sources(pack,documents,lambda identity,run:get('/documents/'+identity+'/structure',params={'parse_run_id':run}))
        status=get('/model/status')
        if args.live_model and not status.get('ready'):
            raise SystemExit('Provider is not configured. No /ask requests sent.')
        manifest={**current,'run_id':run_id,'started_at':datetime.now(timezone.utc).isoformat(),
                  'api_health':get('/health'), 'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  'git_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                  'live_model_requested':args.live_model,'max_generation_requests':len(chosen) if args.live_model else 0,
                  'max_reserved_output_tokens':1600*len(chosen) if args.live_model else 0,
                  'model':{key:status.get(key) for key in ('profile','endpoint_host','model')},
                  'scope':pack['scope'],'case_ids':[case['id'] for case in chosen],
                  'sources':{key:{'sha256':b['document']['sha256'],'document_id':b['document']['id'],
                                  'version_label':b['document']['version_label'],'parse_run_id':b['parse_run_id']}
                             for key,b in bound.items()},
                  'method':'sequential; no retries; no gold in requests; no product tuning between selected holdout queries; small Chinese factual draft set',
                  'quality_notice':pack['holdout_limit']}
        (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        for case in chosen:
            row={'case_id':case['id'],'split':case['split'],'case_sha256':digest(case),
                 'gold_groups':len(case['gold_groups']),'live_model_requested':args.live_model}
            started=time.perf_counter()
            try:
                result=client.post('/ask',json=request_body(case,bound,args.live_model))
                result.raise_for_status();response=result.json()
                row.update(response_sha256=digest(response),response=response,score=score_response(case,response,bound))
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as error:
                # Provider bodies, headers and exception strings may contain secrets.
                row['error_type']=type(error).__name__
            row['elapsed_seconds']=round(time.perf_counter()-started,4);rows.append(row)
            (target/'raw.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
            print(case['id']+': '+json.dumps(row.get('score',{'error_type':row.get('error_type')}),ensure_ascii=False),flush=True)
    reviews=[]  # Review saved responses offline with review_evaluation.py; never rerun to grade.
    report={'manifest':manifest,'overall':summarize(rows,reviews),
            'by_split':{split:summarize([row for row in rows if row['split']==split],reviews)
                        for split in sorted({row['split'] for row in rows})},
            'cases':[{key:value for key,value in row.items() if key!='response'} for row in rows]}
    (target/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    template=[{'case_id':row['case_id'],'case_sha256':row['case_sha256'],
               'response_sha256':row.get('response_sha256'),'status':'pending','reviewer':'',
               'judgment':None,'supported_claims':None,'notes':''} for row in rows]
    (target/'review-template.json').write_text(json.dumps(template,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved immutable run folder:',target)
    print(json.dumps(report['overall'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
