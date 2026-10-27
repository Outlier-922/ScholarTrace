"""Real provider smoke check. Optional import is explicit; never enables tracking."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from pathlib import Path
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--import-arxiv',default=None,help='Explicit public arXiv ID to import; omitted by default')
    parser.add_argument('--check-topic',default=None,help='Save a paused topic and manually check twice; no automatic checks')
    parser.add_argument('--output',type=Path,default=Path('data/stage4_official_report.json'))
    args=parser.parse_args()
    report={'searches':[]}
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=180) as client:
        def post(route,body):
            response=client.post(route,json=body);response.raise_for_status();return response.json()
        for query in ['1706.03762v7','10.18653/v1/N19-1423','Attention Is All You Need']:
            result=post('/literature/search',{'query':query,'limit':3})
            assert result['items'],result['providers']
            if query=='Attention Is All You Need':
                assert any(item['key'].startswith('arxiv:1706.03762v') for item in result['items']),result['providers']
            report['searches'].append({'query':query,'providers':result['providers'],'items':[{key:item.get(key) for key in ['key','title','identifier','authors','year','fulltext_status']} for item in result['items']]})
        if args.import_arxiv:
            search=post('/literature/search',{'query':args.import_arxiv,'limit':3})
            assert len(search['items'])==1
            paper=search['items'][0]
            response=post('/literature/import',{'key':paper['key'],'confirm_identity':True})
            identity=response['document']['id']
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                document=client.get('/documents/'+identity).json()
                if document['status'] in ['ready','partial','failed']:break
                time.sleep(.3)
            assert document['status'] in ['ready','partial'],document
            pdf=client.get('/documents/'+identity+'/pdf');pdf.raise_for_status()
            assert hashlib.sha256(pdf.content).hexdigest()==response['provenance']['sha256']
            assert client.get('/documents/'+identity+'/provenance').json()['items']
            report['import']={'key':paper['key'],'title':paper['title'],'document_id':identity,'status':document['status'],
                              'pages':document['page_count'],'blocks':document['blocks_count'],'sha256':document['sha256'],
                              'outcome':response['document'].get('upload_outcome'),'provenance':response['provenance']}
        if args.check_topic:
            topic=post('/literature/subscriptions',{'query':args.check_topic,'enabled':False})
            first=post('/literature/subscriptions/'+topic['id']+'/check',{})
            second=post('/literature/subscriptions/'+topic['id']+'/check',{})
            report['tracking']={'query':topic['query'],'enabled':False,'first_status':first['status'],'first_count':len(first['items']),
                                'second_status':second['status'],'second_count':len(second['items']),'warnings':second['warnings']}
        report['health']=client.get('/health').json()
    args.output.parent.mkdir(exist_ok=True,parents=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
