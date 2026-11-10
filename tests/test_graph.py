"""Graph provenance, direction and missing-evidence tests; papers below are fictional."""
from __future__ import annotations
import json
import uuid

import httpx
import pymupdf
import pytest

from conftest import upload_pdf,wait_for_document


@pytest.fixture
def graph_papers(client,tmp_path):
    papers=[]
    for label,identity in [('Alpha','2605.11111v1'),('Beta','2605.44444v1')]:
        path=tmp_path/(identity+'.pdf')
        with pymupdf.open() as pdf:
            pdf.set_metadata({'title':'SYNTHETIC Citation Source '+label+' '+uuid.uuid4().hex})
            page=pdf.new_page()
            page.insert_text((54,60),'SYNTHETIC Citation Source '+label,fontsize=18)
            page.insert_text((54,120),'Fictional software test. The method cites [1] and [2].')
            page.insert_text((54,180),'We plan to explore synthetic graph methods in future work. No experiment was run.')
            page=pdf.new_page();page.insert_text((54,60),'References',fontsize=18)
            page.insert_text((54,110),'[1] Fixture Writer. Synthetic Shared Target. arXiv:2605.22222, 2026.')
            page.insert_text((54,160),'[2] Another Writer. Synthetic Other Target. arXiv:2605.33333, 2026.')
            page.insert_text((54,210),'[3] Fixture Without Identifier. Fictional bibliography. 2026.')
            pdf.save(path)
        papers.append(wait_for_document(client,upload_pdf(client,path)['id']))
    from backend.app import literature,literature_store
    record={'key':'arxiv:2605.22222v1','provider':'arxiv','identifier':'2605.22222v1','title':'SYNTHETIC Shared Target','year':2026,'authors':['Fixture Writer'],
            'retrieved_at':'2026-01-01T00:00:00+00:00','landing_url':'https://arxiv.org/abs/2605.22222v1','source_url':'https://example.test/synthetic-registry'}
    literature_store.save_records([record])
    return papers


def build(client,papers,**options):
    response=client.post('/api/graphs',json={'document_ids':[paper['id'] for paper in papers],'resolve_metadata':False,**options})
    assert response.status_code==200,response.text
    return response.json()


def test_graph_edges_bind_pdf_blocks_and_do_not_invent_unresolved_identity(client,graph_papers):
    graph=build(client,graph_papers)
    assert len(graph['sources'])==2
    assert len(graph['edges'])==4
    assert graph['metrics']['eligible_edges']==2
    assert len(graph['unresolved_references'])==2
    unknown=next(node for node in graph['nodes'] if node['id']=='arxiv:2605.33333')
    assert unknown['identity_status']=='identifier_unresolved' and unknown['pagerank'] is None
    shared=next(node for node in graph['nodes'] if node['id']=='arxiv:2605.22222')
    assert shared['in_degree']==2 and shared['metadata_only'] is True
    assert graph['metrics']['shared_ancestors'][0]['node_id']==shared['id']
    assert all(edge['source']!=edge['target'] for edge in graph['edges'])
    for edge in graph['edges']:
        for evidence in edge['evidence']:
            structure=client.get(f"/api/documents/{evidence['source_document_id']}/structure?parse_run_id={evidence['parse_run_id']}").json()
            blocks={block['id']:block for block in structure['blocks']}
            assert evidence['body_citations']
            for anchor in evidence['reference_sources']:
                assert anchor['text']==blocks[anchor['block_id']]['text']
                assert anchor['bbox']==blocks[anchor['block_id']]['bbox']
    assert sum(node['pagerank'] or 0 for node in graph['nodes'])==pytest.approx(1)


def test_pagerank_uses_incoming_direction_deduplicates_pairs_and_handles_dangling():
    from backend.app.graph import pagerank
    edges=[{'source':'A','target':'B'}]*4
    result,iterations,converged=pagerank(['A','B'],edges)
    assert converged and iterations>1
    assert result['A']==pytest.approx(1/2.85)
    assert result['B']>result['A'] and sum(result.values())==pytest.approx(1)
    assert pagerank([],[])[0]=={}
    assert pagerank(['A','B'],[])[0]=={'A':.5,'B':.5}


def test_local_roles_ignore_unresolved_edges_and_external_nodes():
    from backend.app import graph

    def node(identity, rank, kind='local'):
        return {'id': identity, 'kind': kind, 'pagerank': rank}

    def edge(source, target, status='identifier_resolved'):
        return {'id': source + '-' + target, 'source': source, 'target': target, 'status': status}

    nodes = [node('core', .4), node('peripheral', .2), node('other', .1),
             node('isolated', .1), node('external', .2, 'external')]
    edges = [edge('peripheral', 'core'), edge('other', 'core', 'local_title_author_matched'),
             edge('other', 'core'), edge('isolated', 'core', 'identifier_unresolved'),
             edge('external', 'peripheral'), edge('core', 'core')]
    roles = graph.local_roles(nodes, edges)
    assert roles['cornerstones'] == [{'node_id': 'core', 'local_cited_by': 2}]
    assert {item['node_id'] for item in roles['peripheral']} == {'peripheral', 'other'}
    for item in roles['peripheral']:
        assert item['local_cited_by'] == 0
        assert item['links'] == [{'target_id': 'core', 'edge_id': item['node_id'] + '-core'}]


def test_versions_collapse_to_latest_and_selection_is_explicit(monkeypatch):
    from backend.app import graph
    documents=[{'id':'v1','arxiv_id':'2605.11111','version_label':'v1','filename':'paper-v1.pdf','created_at':'2026','parse_run_id':'r1'},
               {'id':'v7','arxiv_id':'2605.11111','version_label':'v7','filename':'paper-v7.pdf','created_at':'2026','parse_run_id':'r7'},
               {'id':'scan','arxiv_id':'2605.11111','version_label':'v7','filename':'paper-v7_img.pdf','created_at':'2026','parse_run_id':'rs'}]
    assert graph.choose_sources(documents)[0]['document']['id']=='v7'
    assert graph.choose_sources(documents,{'arxiv:2605.11111':'v1'})[0]['document']['id']=='v1'
    with pytest.raises(ValueError):graph.choose_sources(documents,{'arxiv:2605.11111':'unknown'})
    with pytest.raises(ValueError):graph.choose_sources(documents,{'unknown':'v1'})


def test_continuations_are_joined_and_appendix_identifiers_are_excluded():
    from backend.app.graph import bibliography_units
    def block(identity,text,kind='paragraph'):return {'id':identity,'text':text,'type':kind,'page':1,'bbox':[0,0,1,1]}
    blocks=[block('head','References','heading'),block('one','[1] Fixture Writer.'),block('cont','A synthetic title. arXiv:2605.22222.'),block('table','Generated table candidate text','table'),block('appendix','Attention Visualizations','heading'),block('notref','arXiv:2605.99999')]
    blocks[2]['metadata']={'extraction':'rapidocr','confidence':.82}
    refs=[{'id':item['id'],'block_id':item['id'],'text':item['text'],'marker':'1' if item['id']=='one' else None} for item in blocks[1:] if item['id']!='appendix']
    units=bibliography_units({'blocks':blocks,'references':refs})
    assert len(units)==1 and len(units[0]['sources'])==2
    assert '2605.22222' in units[0]['text'] and '2605.99999' not in units[0]['text']
    assert 'Generated table' not in units[0]['text']
    assert units[0]['sources'][1]['extraction']=='rapidocr' and units[0]['sources'][1]['confidence']==.82


def test_registry_title_and_author_conflict_is_not_counted_as_verified_edge(client,graph_papers):
    from backend.app import graph
    sources,units=graph.collect_sources(graph.choose_sources(graph_papers))
    target='arxiv:2605.22222'
    record={'title':'Completely unrelated physics investigation','authors':['Unrelated Scientist'],'year':2020,'landing_url':'https://example.test/record'}
    result=graph.assemble(sources,[units[0]],{target:record},'synthetic',20)
    assert result['edges'][0]['status']=='identity_conflict'
    assert result['metrics']['eligible_edges']==0


def test_ambiguous_multi_identifier_fragment_stays_outside_graph(client,graph_papers):
    from backend.app import graph
    chosen=graph.choose_sources(graph_papers)
    sources,units=graph.collect_sources(chosen)
    unit=units[0];unit['identifiers']=[('arxiv:2605.22222','2605.22222'),('arxiv:2605.33333','2605.33333')]
    result=graph.assemble(sources,[unit],graph.registry_index(graph.cached_records()),'synthetic',20)
    assert result['edges']==[] and len(result['unresolved_references'])==1


def test_arxiv_doi_alias_is_one_identity_not_two():
    from backend.app.graph import identifiers
    values=identifiers('arXiv:2605.22222 and https://doi.org/10.48550/arXiv.2605.22222')
    assert {key for key,_ in values}=={'arxiv:2605.22222'}


def test_graph_snapshot_survives_reparse_and_export_keeps_original_run(client,graph_papers):
    snapshot=build(client,graph_papers)
    source=graph_papers[0];old=source['parse_run_id']
    response=client.post(f"/api/documents/{source['id']}/reprocess",json={'ocr':False,'tables':False})
    assert response.status_code==200
    wait_for_document(client,source['id'])
    restored=client.get(f"/api/graphs/{snapshot['id']}").json()
    assert source['id'] in restored['newer_source_runs']
    assert next(item for item in restored['sources'] if item['document_id']==source['id'])['parse_run_id']==old
    exported=client.get(f"/api/graphs/{snapshot['id']}/export?format=markdown")
    assert exported.status_code==200 and old in exported.text and 'arxiv:' not in exported.headers.get('content-disposition','')
    assert client.get(f"/api/graphs/{snapshot['id']}/export").json()['edges']==snapshot['edges']


def test_user_purpose_annotation_does_not_turn_into_automatic_verification(client,graph_papers):
    snapshot=build(client,graph_papers);edge=snapshot['edges'][0]
    response=client.patch(f"/api/graphs/{snapshot['id']}/edges/{edge['id']}",json={'purpose':'method'})
    assert response.status_code==200
    updated=next(item for item in response.json()['edges'] if item['id']==edge['id'])
    assert updated['purpose']=='method' and updated['purpose_status']=='user_labeled'
    assert response.json()['metrics']==snapshot['metrics']
    assert client.patch(f"/api/graphs/{snapshot['id']}/edges/{edge['id']}",json={'purpose':'proven_breakthrough'}).status_code==422


def test_outline_has_only_local_source_excerpts_and_future_direction_is_unchecked(client,graph_papers):
    snapshot=build(client,graph_papers,topic='synthetic graph future work')
    outline=client.get(f"/api/graphs/{snapshot['id']}/outline").json()
    assert outline['mode']=='evidence_outline' and outline['future_work']
    assert all(item['verification_status']=='not_checked' for item in outline['future_work'])
    documents={paper['id'] for paper in graph_papers}
    assert all(item['document_id'] in documents for section in outline['sections'] for item in section['evidence'])
    assert all(section['status']=='source_excerpts_only' for section in outline['sections'])


def test_future_work_markers_survive_pdf_linebreaks(monkeypatch):
    from backend.app import graph
    source={'document_id':'fixture','parse_run_id':'old','title':'SYNTHETIC source'}
    block={'id':'paragraph','page':1,'bbox':[0,0,1,1],'text':'We\nplan to study imaginary directions in future\nwork.','type':'paragraph'}
    monkeypatch.setattr(graph.store,'get_document',lambda identity:{'id':identity})
    monkeypatch.setattr(graph.store,'get_structure',lambda *args,**kwargs:{'blocks':[block]})
    monkeypatch.setattr(graph.qa,'_retrieve',lambda *args,**kwargs:[])
    outline=graph.outline_payload({'id':'fixture','topic':'synthetic','sources':[source],'metrics':{'shared_ancestors':[]}})
    assert len(outline['future_work'])==1 and outline['future_work'][0]['text']==block['text']


def test_registration_failure_retains_pending_edges_without_fabricating_metadata(client,graph_papers,monkeypatch):
    from backend.app import literature
    async def failed(*args,**kwargs):raise httpx.ConnectError('injected registry unavailable')
    monkeypatch.setattr(literature,'_fetch',failed)
    snapshot=build(client,graph_papers,resolve_metadata=True)
    assert any('请求失败' in warning for warning in snapshot['warnings'])
    assert any(edge['status']=='identifier_unresolved' for edge in snapshot['edges'])


def test_batch_resolution_accepts_only_requested_identifiers(client,graph_papers,monkeypatch):
    from backend.app import literature
    async def injected(provider,params=None,doi=None):
        return b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2605.33333v1</id><title>SYNTHETIC Other</title><published>2026-01-01</published></entry><entry><id>http://arxiv.org/abs/2605.99999v1</id><title>UNREQUESTED FIXTURE</title><published>2026-01-01</published></entry></feed>'''
    monkeypatch.setattr(literature,'_fetch',injected)
    snapshot=build(client,graph_papers,resolve_metadata=True)
    assert next(node for node in snapshot['nodes'] if node['id']=='arxiv:2605.33333')['identity_status']=='registry_resolved'
    assert all('99999' not in node['id'] for node in snapshot['nodes'])


def test_source_deleted_during_registry_request_cannot_be_saved(client,graph_papers,monkeypatch):
    from backend.app import graph,store
    async def removed(keys,budget):
        store.delete_document(graph_papers[0]['id'])
        return {},[]
    monkeypatch.setattr(graph,'resolve_identifiers',removed)
    response=client.post('/api/graphs',json={'document_ids':[paper['id'] for paper in graph_papers],'resolve_metadata':True})
    assert response.status_code==409


def test_deleting_graph_source_invalidates_graph_outline_and_export(client,graph_papers):
    snapshot=build(client,graph_papers)
    client.delete('/api/documents/'+graph_papers[0]['id'])
    for suffix in ['', '/outline','/export']:
        assert client.get(f"/api/graphs/{snapshot['id']}"+suffix).status_code==404
    assert snapshot['id'] not in {item['id'] for item in client.get('/api/graphs').json()['items']}


def test_followup_reports_candidates_not_solution_or_novelty(client,graph_papers,monkeypatch):
    from backend.app import literature
    snapshot=build(client,graph_papers)
    evidence=client.get(f"/api/graphs/{snapshot['id']}/outline").json()['future_work'][0]
    async def injected(body):return {'items':[{'key':'fixture','title':'SYNTHETIC later candidate'}],'providers':[{'provider':'fixture','status':'ok'}]}
    monkeypatch.setattr(literature,'search',injected)
    response=client.post(f"/api/graphs/{snapshot['id']}/followup",json={'block_id':evidence['block_id'],'query':'synthetic graph methods'})
    assert response.status_code==200 and response.json()['status']=='candidate_search_only'
    assert response.json()['source']['parse_run_id']==evidence['parse_run_id']
    assert '不能认定' in response.json()['conclusion']
    saved=client.get(f"/api/graphs/{snapshot['id']}/followups").json()['items']
    assert len(saved)==1 and saved[0]['id']==response.json()['id']
    assert client.post(f"/api/graphs/{snapshot['id']}/followup",json={'block_id':'forged','query':'synthetic methods'}).status_code==422


def test_followup_keeps_dates_uncertain_and_does_not_treat_source_or_earlier_work_as_progress():
    from backend.app.graph import screen_followup
    payload={'nodes':[{'id':'arxiv:1706.03762','document_id':'source','year':2017}]}
    source={'document_id':'source','title':'Attention Is All You Need'}
    candidates=[{'key':'arxiv:1706.03762v7','title':source['title'],'year':2017},
                {'key':'old','title':'SYNTHETIC earlier','year':2010},
                {'key':'same','title':'SYNTHETIC same-year','year':2017},
                {'key':'new','title':'SYNTHETIC later','year':2020},
                {'key':'unknown','title':'SYNTHETIC undated'}]
    kept,screening=screen_followup(payload,source,candidates)
    assert len(screening['excluded'])==2
    assert [item['temporal_review'] for item in kept]==['same_year_uncertain','later_year_candidate','date_unknown']


@pytest.mark.parametrize('body',[{'document_ids':[]},{'document_ids':['missing']},{'document_ids':['missing'],'max_nodes':1000}])
def test_invalid_graph_requests_fail_without_network(client,body):
    assert client.post('/api/graphs',json=body).status_code in {404,422}
