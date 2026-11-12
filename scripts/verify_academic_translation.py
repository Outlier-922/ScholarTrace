"""Explicit real-model evaluation; reports do not automatically certify semantics."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8016')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-real-model',action='store_true')
    args=parser.parse_args()
    if not args.run_real_model:parser.error('Use --run-real-model to authorize up to three configured-provider requests')
    if args.output.exists():parser.error('Choose a fresh output file')
    cases=json.loads((Path(__file__).resolve().parents[1]/'evaluation/academic_translation_cases.json').read_text(encoding='utf-8'))
    results=[]
    with httpx.Client(base_url=args.base_url,timeout=90,trust_env=False) as client:
        for case in cases['cases']:
            body={key:case[key] for key in ['title','text','glossary']}
            response=client.post('/api/academic-translations',json={**body,'use_model':True})
            if response.status_code!=200:raise RuntimeError(f"Case {case['id']} HTTP {response.status_code}; no provider error body logged")
            result=response.json()
            if result['status'] not in {'translated','cached'}:raise RuntimeError('Model unavailable; source-only result is not a translation')
            results.append({'case':case,'result':result,'semantic_review':'pending'})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'scope':cases['scope'],'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':case['id'],'status':result['status'],'translation':result['translation'],'usage':result['usage']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
