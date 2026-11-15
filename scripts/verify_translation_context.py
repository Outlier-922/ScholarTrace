"""Bounded official-paper translation probe, preserving first outputs."""
import argparse
import json
from pathlib import Path
import time
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live-model',action='store_true')
    parser.add_argument('--block-ids',nargs='+')
    args=parser.parse_args()
    if not args.live_model:parser.error('Requires explicit --live-model; at most four new requests')
    if args.output.exists():parser.error('Choose a fresh output directory')
    args.output.mkdir(parents=True)
    def save(name,value):(args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=90,trust_env=False) as client:
        def get(path):
            r=client.get(path);r.raise_for_status();return r.json()
        def post(path,body):
            r=client.post(path,json=body)
            if r.status_code!=200:raise RuntimeError(f'HTTP {r.status_code}; response body withheld')
            return r.json()
        doc=next(d for d in get('/documents')['items'] if d['filename']=='1910.01108v4.pdf')
        body={'document_id':doc['id'],'parse_run_id':doc['parse_run_id'],'scope':'blocks','target_language':'zh',
              'block_ids':['blk_835ed33d9b09485c9f5aab7bc56c4644','blk_58fceccf5dc5438eaf9de4362e7abe0d',
                           'blk_e9ce5846ddb14f55adbfa9595d2072c9','blk_0a84131a1f8a4baa9d75df7ccff24a8d']}
        if args.block_ids:
            if not set(args.block_ids)<=set(body['block_ids']):raise ValueError('Unknown probe block')
            body['block_ids']=list(dict.fromkeys(args.block_ids))
        job=post('/document-translations',body);save('plan.json',job)
        def run(job,allow,calls,units):
            post('/document-translations/'+job['id']+'/control',{'action':'start' if job['status']=='planned' else 'resume',
                 'expected_revision':job['revision'],'allow_model':allow,'max_model_calls':calls,
                 'max_output_tokens':3500*calls,'max_units':units,'max_active_seconds':120})
            deadline=time.monotonic()+150
            while time.monotonic()<deadline:
                detail=get('/document-translations/'+job['id'])
                if detail['status'] not in {'queued','running','pause_requested','cancel_requested'}:return detail
                time.sleep(.25)
            raise RuntimeError('Translation batch timed out')
        first=run(job,True,1,1);save('first-batch.json',first)
        if first['status'] not in {'budget_exhausted','completed'}:raise RuntimeError('First batch stopped; no retry')
        final=first if first['status']=='completed' else run(first,True,3,3)
        save('final.json',final)
        if final['status']!='completed':raise RuntimeError('Final batch stopped; no retry')
        replay=run(post('/document-translations',body),False,0,10);save('cache-replay.json',replay)
        assert replay['status']=='completed' and replay['totals']['calls']==0
        exported=client.get('/document-translations/'+final['id']+'/export');exported.raise_for_status()
        (args.output/'translation.zip').write_bytes(exported.content)
        print(json.dumps({'job':final['id'],'counts':final['counts'],'usage':final['totals'],
                          'cache_replay':replay['counts'],'semantic_review':'pending'},ensure_ascii=False))


if __name__=='__main__':main()
