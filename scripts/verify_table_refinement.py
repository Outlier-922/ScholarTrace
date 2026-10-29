"""Zero-model local smoke on explicitly selected, already observed official tables."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--api',default='http://127.0.0.1:8777/api')
    p.add_argument('--pdf',type=Path,required=True)
    p.add_argument('--freeze',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if urlsplit(args.api).hostname not in {'127.0.0.1','localhost','::1'}:p.error('Only loopback APIs are supported.')
    if args.output.exists():p.error('Preserve prior runs; choose a new output directory.')
    frozen=json.loads(args.freeze.read_text(encoding='utf-8'))
    for name,sha in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()!=sha:raise SystemExit('Frozen code changed: '+name)
    args.output.mkdir(parents=True)
    def save(name,value):(args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    with httpx.Client(base_url=args.api,timeout=90,trust_env=False) as c:
        sha=hashlib.sha256(args.pdf.read_bytes()).hexdigest()
        save('manifest.json',{'source_sha256':sha,'source_filename':args.pdf.name,'freeze':str(args.freeze),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'model_calls':0,'scope':'Known development examples; manual regions/cuts explicit. Not recognition accuracy.'})
        response=c.post('/documents',files={'files':(args.pdf.name,args.pdf.read_bytes(),'application/pdf')});response.raise_for_status();doc=response.json()['items'][0]
        for _ in range(200):
            doc=c.get('/documents/'+doc['id']).json()
            if doc['status'] in {'ready','partial','failed'}:break
            time.sleep(.2)
        if doc['status'] not in {'ready','partial'}:raise SystemExit('Native parse not ready.')
        items=[]
        rounded=lambda n:round(n*100,4)/100
        for name,page in [('multicolumn',9),('merged_rows',10),('borderless_auto',8),('borderless_manual_cuts',8)]:
            if page!=8:
                o=c.get('/documents/'+doc['id']+'/table-options',params={'page':page,'parse_run_id':doc['parse_run_id']}).json()['items']
                if len(o)!=1:raise ValueError('Expected a single explicit existing candidate; inspect source.')
                option=o[0];box=[rounded(v) for v in option['bbox']];end=rounded(option['header_bottom']);anchor=option['block_id']
            else:box=[rounded(130/612),rounded(96/792),rounded(480/612),rounded(244/792)];end=rounded(128/792);anchor=None
            cuts=[rounded(v/612) for v in [280,325,375,430]] if name=='borderless_manual_cuts' else None
            body={'document_id':doc['id'],'parse_run_id':doc['parse_run_id'],'page':page,'bbox':box,'header_bottom':end,'anchor_block_id':anchor,'column_cuts':cuts}
            response=c.post('/table-regions',json=body);response.raise_for_status();source=response.json();g=source['grid']
            cached=c.post('/table-regions',json=body);cached.raise_for_status();assert cached.json()['id']==source['id']
            all_ids={w['id'] for w in source['words']};used={w for col in g['columns'] for w in col['word_ids']}|{w for cell in g['cells'] for w in cell['word_ids']}|{w for row in g['unassigned'] for w in row['word_ids']}
            assert all_ids==used,'Native words must remain accounted for.'
            revision=None
            if name=='borderless_manual_cuts':
                rows=[r[:] for r in g['rows']]
                # Explicit developer transcription from the inspected source image;
                # shared cost cells deliberately remain null, never copied twice.
                rows[-2]=['Transformer (base model)','27.3','38.1',None,None]
                rows[-1]=['Transformer (big)','28.4','41.8',None,None]
                revised=c.post('/table-regions/'+source['id']+'/revisions',json={'rows':rows,'labels':[col['label'] for col in g['columns']],'note':'开发助手按原图移录两行模型名和BLEU；合并训练成本保留null，不按语言重复填值；尚未独立核验。'});revised.raise_for_status();revision=revised.json()
            params={'revision_id':revision['id']} if revision else {}
            for format in ['csv','json']:
                exported=c.get('/table-regions/'+source['id']+'/export',params={'format':format,**params});exported.raise_for_status()
                (args.output/(name+'.'+format)).write_bytes(exported.content)
            (args.output/(name+'.png')).write_bytes(c.get('/table-regions/'+source['id']+'/image').content)
            items.append({'name':name,'request':body,'source':source,'revision':revision,'cached':True})
            save('records.json',{'model_calls':0,'items':items,'semantic_verified':False})
            print(json.dumps({'name':name,'source_id':source['id'],'rows':len(g['rows']),'columns':len(g['columns']),'unassigned':len(g['unassigned'])}))


if __name__=='__main__':main()
