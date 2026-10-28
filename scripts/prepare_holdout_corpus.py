"""Import exact versions for the next work-disjoint draft evaluation, then index locally."""
import argparse
import json
from pathlib import Path
import time
import httpx

PAPERS=[
    ('1910.01108v4','DistilBERT, a distilled version of BERT: smaller, faster, cheaper and lighter'),
    ('2103.00020v1','Learning Transferable Visual Models From Natural Language Supervision'),
]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    args=parser.parse_args();records=[]
    with httpx.Client(base_url=args.api,timeout=180,trust_env=False) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response.json()
        def post(path,body):
            response=client.post(path,json=body);response.raise_for_status();return response.json()
        for arxiv_id,title in PAPERS:
            result=post('/literature/search',{'query':'arxiv:'+arxiv_id,'provider':'arxiv','limit':1})
            item=next(item for item in result['items'] if item['identifier']==arxiv_id)
            assert item['title'].casefold().rstrip('.')==title.casefold()
            imported=post('/literature/import',{'key':item['key'],'confirm_identity':True})
            identity=imported['document']['id'];deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                document=get('/documents/'+identity)
                if document['status'] in {'ready','partial','failed'}:break
                time.sleep(.3)
            assert document['status'] in {'ready','partial'},document['status']
            job=post('/semantic-index-jobs',{'document_id':identity,'parse_run_id':document['parse_run_id']})
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                job=get('/semantic-index-jobs/'+job['id'])
                if job['status'] not in {'queued','running'}:break
                time.sleep(.5)
            assert job['status']=='completed',job
            records.append({'identifier':arxiv_id,'verified_title':title,'document':document,
                            'provenance':imported['provenance'],'index':job})
            Path('data/stage10_corpus.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            print(arxiv_id+': '+str(document['page_count'])+' pages; '+str(job['done'])+' chunks indexed.',flush=True)
    target=Path('data/stage10_corpus.json')
    target.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved provenance and indexing report:',target)


if __name__=='__main__':main()
