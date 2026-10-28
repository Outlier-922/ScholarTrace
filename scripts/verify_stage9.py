"""Explicit real-paper reparse/index acceptance; no paid generation requests."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    args=parser.parse_args();output=Path('data/stage9_verification');output.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=args.api,timeout=180,trust_env=False) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response.json()
        def post(path,body):
            response=client.post(path,json=body);response.raise_for_status();return response.json()
        document=next(d for d in get('/documents')['items'] if d['filename']=='1910.10683v4.pdf')
        identity=document['id'];old_run=document['parse_run_id']
        before=get('/documents/'+identity+'/structure',params={'parse_run_id':old_run})
        old_hash=sha(before['blocks'])
        new=post('/documents/'+identity+'/reprocess',{'ocr':False,'tables':True})
        print('New ParseRun:',new['parse_run_id'],flush=True)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            new=get('/documents/'+identity)
            if new['status'] in {'ready','partial','failed'}:break
            time.sleep(.3)
        assert new['status']=='ready',new['status']
        structure=get('/documents/'+identity+'/structure')
        assert structure['parse_run_id']!=old_run
        old=get('/documents/'+identity+'/structure',params={'parse_run_id':old_run})
        assert sha(old['blocks'])==old_hash
        pairs=[]
        for old_block in before['blocks']:
            if old_block['page'] not in {11,12}:continue
            candidates=[b for b in structure['blocks'] if b['page']==old_block['page'] and b['text']==old_block['text'] and b['bbox']==old_block['bbox']]
            for block in candidates:
                layout=block.get('metadata',{}).get('layout_text','')
                if '2^{19}' in layout or '2^{35}' in layout:
                    pairs.append({'old_block':old_block['id'],'new_block':block['id'],'page':block['page'],
                                  'raw_text_sha256':hashlib.sha256(block['text'].encode()).hexdigest(),
                                  'superscript_19':'2^{19}' in layout,'superscript_35':'2^{35}' in layout})
        assert any(p['superscript_19'] for p in pairs) and any(p['superscript_35'] for p in pairs)
        report={'document_id':identity,'pdf_sha256':new['sha256'],'old_run':old_run,'new_run':structure['parse_run_id'],
                'historical_blocks_unchanged':True,'historical_blocks_hash':old_hash,'matched_raw_layout_blocks':pairs,
                'parser_version':structure['parse_run']['parser_version']}
        (output/'reparse.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Historical snapshot, raw text/boxes and superscript metadata checked.',flush=True)
        job=post('/semantic-index-jobs',{'document_id':identity,'parse_run_id':structure['parse_run_id']})
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            job=get('/semantic-index-jobs/'+job['id'])
            if job['status'] not in {'queued','running'}:break
            time.sleep(.5)
        assert job['status']=='completed',job
        (output/'index.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Index completed:',job['done'],'/',job['total'],flush=True)


if __name__=='__main__':main()
