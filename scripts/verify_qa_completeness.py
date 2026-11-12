"""Bounded official-PDF completeness probe, with no expected answers in prompts."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live-model',action='store_true')
    parser.add_argument('--cases',nargs='+')
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output path')
    pack=json.loads((Path(__file__).resolve().parents[1]/'evaluation/qa_completeness_cases.json').read_text(encoding='utf-8'))
    if args.cases:
        if not set(args.cases)<={case['id'] for case in pack['cases']}:parser.error('Unknown case')
        pack['cases']=[case for case in pack['cases'] if case['id'] in args.cases]
    rows=[]
    with httpx.Client(base_url=args.api,timeout=120,trust_env=False) as client:
        docs=client.get('/documents').json()['items']
        for case in pack['cases']:
            sources=[next(d for d in docs if d['filename']==name) for name in case['files']]
            body={'question':case['question'],'document_ids':[d['id'] for d in sources],
                  'parse_runs':{d['id']:d['parse_run_id'] for d in sources},'use_model':args.live_model,'limit':12}
            response=client.post('/ask',json=body)
            if response.status_code!=200:raise RuntimeError(f"Case {case['id']} HTTP {response.status_code}")
            result=response.json()
            rows.append({'case':case,'request':body,'sources':[{k:d.get(k) for k in ['id','filename','sha256','parse_run_id']} for d in sources],'result':result})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'scope':pack['scope'],'live_model':args.live_model,'cases':rows},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'id':case['id'],'mode':result['mode'],'answer':result['answer'],'coverage':result.get('answer_coverage'),'missing':result.get('missing_information')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
