"""HTTP acceptance against imported real PDFs and explicitly fictional CSV."""
from __future__ import annotations
import argparse
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import httpx
import pymupdf


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--output',type=Path,default=Path('data/stage6_verification'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=180) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response
        def post(path,body):
            response=client.post(path,json=body);response.raise_for_status();return response.json()
        documents=get('/documents').json()['items']
        names={'1706.03762v7.pdf','N19-1423.pdf','language_understanding_paper.pdf','1710.10903v3.pdf'}
        selected=[document for document in documents if document['filename'] in names]
        assert len(selected)==4,'Import the four official source PDFs first'
        draft=post('/studio/drafts',{'idea':'面向科研助手的可追溯证据检索、方法设计与实验验证计划','document_ids':[document['id'] for document in selected]})
        assert len(draft['sources'])==4 and draft['evidence']
        sources={source['document_id']:source for source in draft['sources']}
        for evidence in draft['evidence']:
            source=sources[evidence['document_id']]
            structure=get(f'/documents/{source["document_id"]}/structure',params={'parse_run_id':source['parse_run_id']}).json()
            original=next(block for block in structure['blocks'] if block['id']==evidence['block_id'])
            assert evidence['text']==original['text'] and evidence['bbox']==original['bbox']
            assert original['type']=='paragraph' and len(original['text'].split())>=25
            assert original['id'] not in {reference['block_id'] for reference in structure['references']}
        sections=[{**section,'paragraphs':[{key:paragraph[key] for key in ['id','kind','text','evidence_ids']} for paragraph in section['paragraphs']]} for section in draft['sections']]
        sections[3]['paragraphs'][0]['text']='拟采用文献解析、版本化证据检索、人工核验与导出模块。后续在固定来源集上比较原文定位正确率、拒答率和延迟；尚未开展该实验。'
        response=client.patch('/studio/drafts/'+draft['id'],json={'expected_revision':1,'title':'可追溯科研助手：方法与验证计划','sections':sections})
        response.raise_for_status();draft=response.json();assert draft['revision']==2
        assert get('/studio/drafts/'+draft['id'],params={'revision':1}).json()['revision']==1
        bundle=get('/studio/drafts/'+draft['id']+'/export',params={'format':'bundle'}).content
        (args.output/'writing-evidence.zip').write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            markdown=archive.read('draft.md').decode('utf-8');bibtex=archive.read('references.bib').decode('utf-8')
            assert all(source['parse_run_id'] in markdown for source in draft['sources'])
            assert '计划（未实施）' in markdown
            (args.output/'draft.md').write_text(markdown,encoding='utf-8')
            (args.output/'references.bib').write_text(bibtex,encoding='utf-8')
        dataset=post('/studio/datasets/demo',{})
        assert dataset['data_status']=='synthetic_demo'
        charts=[]
        for kind in ['table','line','bar','radar','radar3d','scatter3d']:
            spec={'chart_type':kind,'title':'自建演示数据 · '+kind,'x':'model','y':['accuracy'],'repetition':'run','aggregation':'mean','units':{'accuracy':'%','latency_ms':'ms','energy_j':'J'}}
            if kind=='line':spec.update(x='run',group_by='model',repetition=None,aggregation='none',x_kind='numeric')
            if kind=='bar':spec['error_bar']='std'
            if kind in {'radar','radar3d'}:spec.update(y=['accuracy','latency_ms','energy_j'],normalization='minmax',directions={'accuracy':'higher','latency_ms':'lower','energy_j':'lower'})
            if kind=='scatter3d':spec.update(x='parameters_million',y=['accuracy','latency_ms'],repetition=None,aggregation='none',x_kind='numeric',group_by='model')
            artifact=post('/studio/charts',{'dataset_id':dataset['id'],'spec':spec})
            assert artifact['sha256']==dataset['sha256'] and '非实验结果' in artifact['caption']
            folder=args.output/kind;folder.mkdir(exist_ok=True)
            archive_bytes=get('/studio/assets/'+artifact['id']+'/bundle').content
            (args.output/(kind+'-reproduction.zip')).write_bytes(archive_bytes)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:archive.extractall(folder)
            with pymupdf.open(folder/'figure.pdf') as pdf:
                assert 'SYNTHETIC DEMO' in pdf[0].get_text()
                pdf[0].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).save(folder/'pdf-preview.png')
            charts.append({'kind':kind,'id':artifact['id'],'caption':artifact['caption'],'statistics':artifact['statistics'],'warnings':artifact['warnings']})
        run=subprocess.run([sys.executable,str((args.output/'bar'/'render.py').resolve())],capture_output=True,text=True,timeout=60)
        assert run.returncode==0,run.stderr
        assert (args.output/'bar'/'processed.csv').read_bytes()==(args.output/'bar'/'reproduced'/'processed.csv').read_bytes()
        assert (args.output/'bar'/'caption.txt').read_bytes()==(args.output/'bar'/'reproduced'/'caption.txt').read_bytes()
        diagram=post('/studio/diagrams',{'title':'科研助手证据工作流 · 设计草图','nodes':[{'id':'A','label':'文献导入'},{'id':'B','label':'解析与来源定位'},{'id':'C','label':'证据检索'},{'id':'D','label':'人工核验'},{'id':'E','label':'写作与可视化'}],'edges':[{'source':'A','target':'B'},{'source':'B','target':'C'},{'source':'C','target':'D'},{'source':'D','target':'E'}]})
        diagram_bytes=get('/studio/assets/'+diagram['id']+'/bundle').content
        (args.output/'diagram-reproduction.zip').write_bytes(diagram_bytes)
        with zipfile.ZipFile(io.BytesIO(diagram_bytes)) as archive:archive.extractall(args.output/'diagram')
        report={'seconds':round(time.monotonic()-start,3),'draft_id':draft['id'],'draft_revision':draft['revision'],'source_works':len(sources),'source_anchors':len(draft['evidence']),
                'registered_references':sum(reference['metadata'] is not None for reference in draft['references']),'pending_local_sources':sum(reference['metadata'] is None for reference in draft['references']),
                'sources':draft['sources'],'dataset_id':dataset['id'],'data_status':dataset['data_status'],'csv_sha256':dataset['sha256'],'csv_rows':dataset['row_count'],
                'charts':charts,'diagram_id':diagram['id'],'reproduction_verified':'processed CSV and caption byte-identical; PNG appearance may vary by font','model_used':False}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key not in {'sources','charts'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
