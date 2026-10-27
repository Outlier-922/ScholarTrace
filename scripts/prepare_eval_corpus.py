"""Import two explicit public papers for a new work-disjoint evaluation split."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
import httpx

PAPERS=[
    {'id':'1907.11692v1','title':'RoBERTa: A Robustly Optimized BERT Pretraining Approach','role':'holdout'},
    {'id':'1910.10683v4','title':'Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer','role':'holdout'},
]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    args=parser.parse_args();records=[]
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=180) as client:
        for paper in PAPERS:
            response=client.post('/literature/search',json={'query':'arxiv:'+paper['id'],'provider':'arxiv','limit':1})
            response.raise_for_status();items=response.json()['items']
            item=next(item for item in items if item['identifier']==paper['id'])
            assert item['title'].casefold().rstrip('.')==paper['title'].casefold()
            response=client.post('/literature/import',json={'key':item['key'],'confirm_identity':True});response.raise_for_status()
            result=response.json();identity=result['document']['id']
            until=time.monotonic()+180
            while time.monotonic()<until:
                response=client.get('/documents/'+identity);response.raise_for_status();document=response.json()
                if document['status'] in {'ready','partial','failed'}:break
                time.sleep(.3)
            assert document['status'] in {'ready','partial'},document['status']
            records.append({'source':paper,'document':document,'provenance':result['provenance']})
            print(paper['id']+': '+document['status']+' / '+str(document['page_count'])+' pages',flush=True)
    path=Path('data/stage8_eval_corpus.json');path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved import provenance:',path)


if __name__=='__main__':main()
