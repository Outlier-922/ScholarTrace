"""Real PDF graph smoke check. Resolve only literal bibliography identifiers."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--resolve-metadata',action='store_true',help='Explicitly query official registration APIs')
    parser.add_argument('--output',type=Path,default=Path('data/stage5_official_report.json'))
    args=parser.parse_args()
    names={'1710.10903v3.pdf','N19-1423.pdf','language_understanding_paper.pdf'}
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=300) as client:
        docs=client.get('/documents').json()['items']
        selected=[document for document in docs if document['filename'] in names or document.get('arxiv_id')=='1706.03762']
        assert any(document['filename']=='1706.03762v7.pdf' for document in selected),'Import the official Transformer v7 PDF first'
        start=time.monotonic()
        response=client.post('/graphs',json={'document_ids':[document['id'] for document in selected],
            'resolve_metadata':args.resolve_metadata,'resolution_budget':60,'topic':'model architecture training objectives limitations future work','max_nodes':100})
        response.raise_for_status();graph=response.json()
        assert len(graph['nodes'])>=20,graph['warnings']
        assert len({source['node_id'] for source in graph['sources']})==len(graph['sources'])
        source_runs={source['document_id']:source['parse_run_id'] for source in graph['sources']}
        for edge in graph['edges']:
            assert edge['source']!=edge['target']
            for citation in edge['evidence']:
                assert source_runs[citation['source_document_id']]==citation['parse_run_id']
                assert citation['reference_sources']
                assert 'Table candidate on PDF page' not in citation['reference_text']
        resolved_external=sum(node['kind']=='external' and node['identity_status']=='registry_resolved' for node in graph['nodes'])
        if args.resolve_metadata:assert resolved_external>=20,graph['warnings']
        assert abs(sum(node['pagerank'] or 0 for node in graph['nodes'])-1)<1e-8
        outline=client.get('/graphs/'+graph['id']+'/outline');outline.raise_for_status();outline=outline.json()
        assert all(evidence['parse_run_id']==source_runs[evidence['document_id']] for section in outline['sections'] for evidence in section['evidence'])
        assert all(item['verification_status']=='not_checked' for item in outline['future_work'])
        for format in ['json','markdown']:
            exported=client.get('/graphs/'+graph['id']+'/export',params={'format':format});exported.raise_for_status()
            assert all(run in exported.text for run in source_runs.values())
        titles={node['id']:node['title'] for node in graph['nodes']}
        report={'graph_id':graph['id'],'format_version':graph['format_version'],'seconds':round(time.monotonic()-start,3),
            'selected_documents':len(selected),'source_works':len(graph['sources']),'sources':graph['sources'],'nodes':len(graph['nodes']),
            'edges':len(graph['edges']),'eligible_edges':graph['metrics']['eligible_edges'],'resolved_external':resolved_external,
            'identity_conflicts':sum(edge['status']=='identity_conflict' for edge in graph['edges']),
            'unresolved_groups':len(graph['unresolved_references']),'shared_ancestors':len(graph['metrics']['shared_ancestors']),
            'ranking':[{'title':titles[key],'in_degree':next(node['in_degree'] for node in graph['nodes'] if node['id']==key)} for key in graph['metrics']['ranking'][:5]],
            'outline_passages':sum(len(section['evidence']) for section in outline['sections']),'future_work':len(outline['future_work']),
            'pagerank_converged':graph['metrics']['converged'],'warnings':graph['warnings'],'model_used':False}
    args.output.parent.mkdir(exist_ok=True,parents=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
