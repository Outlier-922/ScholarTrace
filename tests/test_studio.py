"""Labelled fictional data test software behavior, never scientific results."""
from __future__ import annotations
import csv
import io
import math
import subprocess
import sys
import zipfile
import uuid
from pathlib import Path
import pymupdf
import pytest
from backend.app import plot_runtime as runtime,studio
from conftest import upload_pdf,wait_for_document


def spec(**kwargs):return studio.ChartSpec(x='model',y=['accuracy'],aggregation='mean',repetition='run',**kwargs).model_dump()


@pytest.mark.parametrize('encoding,delimiter',[('utf-8-sig',','),('utf-16','\t'),('gb18030',';')])
def test_csv_encoding_delimiters_and_identifier_profile(encoding,delimiter):
    profile=runtime.parse_csv(('编号'+delimiter+'值\n001'+delimiter+'1.5\n002'+delimiter+'NA\n').encode(encoding))
    assert profile['encoding']==encoding and profile['delimiter']==delimiter
    assert profile['columns'][0]['type']!='numeric' and profile['columns'][0]['leading_zero_warning']
    assert profile['columns'][1]['missing_count']==1


@pytest.mark.parametrize('raw',[b'x,x\n1,2',b'x,y\n1,2,3',b'x,y\n',b'x,y\n1,\x00',b'x,y\n"unfinished,2'])
def test_malformed_csv_rejected(raw):
    with pytest.raises(ValueError):runtime.parse_csv(raw)


def test_explicit_missing_policy_keeps_gaps_and_finite_values():
    dataset=runtime.parse_csv(b'step,value\n1,10\n2,NaN\n3,12\n4,inf\n5,1e999\n')
    selected=studio.ChartSpec(chart_type='line',x='step',y=['value'],x_kind='numeric').model_dump()
    with pytest.raises(ValueError,match='不会自动插补'):runtime.prepare_chart(dataset,selected)
    prepared=runtime.prepare_chart(dataset,{**selected,'missing_policy':'drop'})
    assert prepared['categories']==['1','2','3','4','5']
    assert prepared['dropped_rows']==[2,4,5] and prepared['used_records']==2
    assert [point['value'] for point in prepared['points']]==[10,12]


def test_repeated_observation_aggregation_and_sample_errors():
    dataset=runtime.parse_csv(studio.demo_bytes());prepared=runtime.prepare_chart(dataset,spec(error_bar='std'))
    first=prepared['points'][0]
    assert first['n']==3 and first['value']==72
    assert first['std']==pytest.approx(.7) and first['sem']==pytest.approx(.7/math.sqrt(3))
    with pytest.raises(ValueError,match='明确选择均值'):runtime.prepare_chart(dataset,{**spec(),'aggregation':'none'})
    with pytest.raises(ValueError,match='重复的实验标识'):runtime.prepare_chart(runtime.parse_csv(b'model,run,accuracy\nA,1,1\nA,1,2\n'),spec())
    with pytest.raises(ValueError,match='至少需要2'):runtime.prepare_chart(runtime.parse_csv(b'model,run,accuracy\nA,1,1\n'),spec(error_bar='sem'))
    with pytest.raises(ValueError,match='重复实验列'):runtime.prepare_chart(dataset,{**spec(error_bar='std'),'repetition':None})


def test_radar_directions_constants_and_bounds_are_explicit():
    dataset=runtime.parse_csv(b'model,a,b,c\nA,1,10,5\nB,3,20,5\n')
    selected=studio.ChartSpec(chart_type='radar',x='model',y=['a','b','c'],normalization='minmax',directions={'a':'higher','b':'lower','c':'higher'}).model_dump()
    prepared=runtime.prepare_chart(dataset,selected)
    values={(point['x'],point['metric']):point['normalized'] for point in prepared['points']}
    assert values[('A','a')]==0 and values[('A','b')]==1 and values[('A','c')]==.5
    with pytest.raises(ValueError,match='指标方向'):runtime.prepare_chart(dataset,{**selected,'directions':{}})
    with pytest.raises(ValueError,match='超出'):runtime.prepare_chart(dataset,{**selected,'normalization':'bounds','bounds':{'a':[0,2],'b':[0,25],'c':[0,10]}})


def test_numeric_x_aliases_do_not_create_overlaid_line_observations():
    dataset=runtime.parse_csv(b'step,value\n1,10\n1.0,20\n')
    selected=studio.ChartSpec(chart_type='line',x='step',y=['value'],x_kind='numeric').model_dump()
    with pytest.raises(ValueError,match='同一数值'):runtime.prepare_chart(dataset,selected)


def test_csv_export_protects_text_formulas_without_corrupting_numbers(tmp_path):
    dataset=runtime.parse_csv(b'model,value\n=HYPERLINK(evil),-2\n')
    selected=studio.ChartSpec(chart_type='table',x='model',y=['value']).model_dump()
    prepared=runtime.prepare_chart(dataset,selected);target=tmp_path/'processed.csv';runtime.write_processed(prepared,target)
    row=list(csv.DictReader(io.StringIO(target.read_text(encoding='utf-8-sig'))))[0]
    assert row['x'].startswith("'=") and float(row['value'])==-2


@pytest.fixture
def demo_dataset(client):
    result=client.post('/api/studio/datasets/demo');assert result.status_code==200,result.text
    return result.json()


@pytest.mark.parametrize('kind',['table','line','bar','radar','radar3d','scatter3d'])
def test_actual_chart_files_and_caption_from_all_templates(client,demo_dataset,kind):
    selected=spec(chart_type=kind,units={'accuracy':'percent'})
    if kind=='line':selected.update(x='run',group_by='model',repetition=None,aggregation='none',x_kind='numeric')
    if kind in {'radar','radar3d'}:selected.update(y=['accuracy','latency_ms','energy_j'],normalization='minmax',directions={'accuracy':'higher','latency_ms':'lower','energy_j':'lower'})
    if kind=='scatter3d':selected.update(x='parameters_million',y=['accuracy','latency_ms'],repetition=None,aggregation='none',x_kind='numeric',group_by='model')
    result=client.post('/api/studio/charts',json={'dataset_id':demo_dataset['id'],'spec':selected});assert result.status_code==200,result.text
    artifact=result.json();assert '自建演示数据，非实验结果' in artifact['caption']
    assert artifact['sha256']==demo_dataset['sha256'] and artifact['used_records']==9
    png=client.get(f'/api/studio/assets/{artifact["id"]}/files/figure.png');assert png.content.startswith(b'\x89PNG')
    svg=client.get(f'/api/studio/assets/{artifact["id"]}/files/figure.svg');assert b'<svg' in svg.content
    pdf=client.get(f'/api/studio/assets/{artifact["id"]}/files/figure.pdf')
    with pymupdf.open(stream=pdf.content,filetype='pdf') as document:
        assert len(document)==1 and 'SYNTHETIC DEMO' in document[0].get_text()
    assert client.get(f'/api/studio/assets/{artifact["id"]}/files/input.csv').status_code==404


def test_reproduction_zip_executes_same_runtime_and_rejects_changed_input(client,demo_dataset,tmp_path):
    artifact=client.post('/api/studio/charts',json={'dataset_id':demo_dataset['id'],'spec':spec(error_bar='std')}).json()
    result=client.get(f'/api/studio/assets/{artifact["id"]}/bundle')
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert {'render.py','requirements.txt','input.csv','manifest.json','processed.csv','figure.pdf'}<=set(archive.namelist())
        archive.extractall(tmp_path)
    assert (tmp_path/'render.py').read_bytes()==Path(runtime.__file__).read_bytes()
    run=subprocess.run([sys.executable,str(tmp_path/'render.py')],capture_output=True,text=True,timeout=45)
    assert run.returncode==0,run.stderr
    assert (tmp_path/'processed.csv').read_bytes()==(tmp_path/'reproduced'/'processed.csv').read_bytes()
    assert (tmp_path/'caption.txt').read_bytes()==(tmp_path/'reproduced'/'caption.txt').read_bytes()
    (tmp_path/'input.csv').write_bytes(b'x,y\n1,2\n')
    failed=subprocess.run([sys.executable,str(tmp_path/'render.py')],capture_output=True,text=True,timeout=15)
    assert failed.returncode!=0 and 'hash changed' in failed.stderr


def test_dataset_integrity_check_and_invalid_chart_are_non_destructive(client,demo_dataset):
    from backend.app import studio_store
    folder=studio_store.folder(demo_dataset['id']);original=(folder/'input.csv').read_bytes()
    (folder/'input.csv').write_bytes(b'x,y\n1,2\n')
    response=client.post('/api/studio/charts',json={'dataset_id':demo_dataset['id'],'spec':spec()})
    assert response.status_code==409
    (folder/'input.csv').write_bytes(original)
    assert client.get('/api/studio/datasets/'+demo_dataset['id']).status_code==200


def test_controlled_diagram_edges_and_mermaid_escaping(client):
    body={'title':'SYNTHETIC design','nodes':[{'id':'A','label':'input "<data>"'},{'id':'B','label':'validation'}],'edges':[{'source':'A','target':'B'}]}
    result=client.post('/api/studio/diagrams',json=body);assert result.status_code==200,result.text
    identity=result.json()['id'];source=client.get(f'/api/studio/assets/{identity}/files/architecture.mmd').text
    assert '&lt;data&gt;' in source and '&quot;' in source and 'A --> B' in source
    body['edges']=[{'source':'A','target':'unknown'}];assert client.post('/api/studio/diagrams',json=body).status_code==422
    body['nodes'][0]['id']='A; system';assert client.post('/api/studio/diagrams',json=body).status_code==422


@pytest.fixture
def draft_fixture(client,tmp_path):
    path=tmp_path/'synthetic-writing.pdf'
    with pymupdf.open() as pdf:
        pdf.set_metadata({'title':'Synthetic Writing Fixture '+uuid.uuid4().hex,'author':'Fixture Author'})
        page=pdf.new_page(width=1200,height=600)
        page.insert_text((40,55),'SYNTHETIC WRITING FIXTURE - NOT A PAPER',fontsize=18)
        page.insert_text((40,100),'The synthetic model uses a training batch size of 32. This entire example contains fictional software test data and no scientific experiment was run. The evidence retrieval method is evaluated only as a software interface in these automated tests.',fontsize=8)
        page=pdf.new_page(width=1200,height=600)
        page.insert_text((40,55),'References',fontsize=18)
        page.insert_text((40,100),'[1] Fixture Writer. Fictional Reference Only. 2099.',fontsize=10)
        pdf.save(path)
    document=wait_for_document(client,upload_pdf(client,path)['id'])
    result=client.post('/api/studio/drafts',json={'idea':'synthetic training model evidence and limitations','document_ids':[document['id']]})
    assert result.status_code==200,result.text
    return document,result.json()


def patch_body(draft):
    return {'expected_revision':draft['revision'],'title':draft['title'],'sections':[{**section,'paragraphs':[{key:paragraph[key] for key in ['id','kind','text','evidence_ids']} for paragraph in section['paragraphs']]} for section in draft['sections']]}


def test_draft_anchors_exports_and_unverified_sources(client,draft_fixture):
    document,draft=draft_fixture
    assert draft['evidence'] and draft['revision']==1
    assert all(item['parse_run_id']==document['parse_run_id'] for item in draft['evidence'])
    structure=client.get('/api/documents/'+document['id']+'/structure').json()
    blocks={block['id']:block for block in structure['blocks']}
    assert all(blocks[item['block_id']]['type']=='paragraph' and len(item['text'].split())>=25 for item in draft['evidence'])
    assert all(item['metadata'] is None for item in draft['references'])
    result=client.get('/api/studio/drafts/'+draft['id']+'/export?format=bundle')
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        text=archive.read('draft.md').decode();bib=archive.read('references.bib').decode()
        assert '计划（未实施）' in text and '书目身份待核对' in text and document['parse_run_id'] in text
        assert '@misc' not in bib and 'doi =' not in bib


def test_draft_optimistic_versions_and_history_are_immutable(client,draft_fixture):
    _,draft=draft_fixture;body=patch_body(draft);body['title']='Edited synthetic plan'
    result=client.patch('/api/studio/drafts/'+draft['id'],json=body);assert result.status_code==200,result.text
    assert result.json()['revision']==2
    assert client.patch('/api/studio/drafts/'+draft['id'],json=body).status_code==409
    assert client.get('/api/studio/drafts/'+draft['id']+'?revision=1').json()['title']==draft['title']
    assert len(client.get('/api/studio/drafts/'+draft['id']+'/revisions').json()['items'])>=2


@pytest.mark.parametrize('mutation',['foreign_source','novel_number','altered_quote'])
def test_bad_citations_and_added_numbers_rejected(client,draft_fixture,mutation):
    _,draft=draft_fixture;body=patch_body(draft)
    paragraph=next(section for section in body['sections'] if section['id']=='related')['paragraphs'][0]
    if mutation=='foreign_source':paragraph['evidence_ids']=['foreign']
    elif mutation=='novel_number':paragraph.update(kind='fact',text='The model scored 987654321.123 points.')
    else:paragraph['text']='An invented quote unrelated to the selected block.'
    result=client.patch('/api/studio/drafts/'+draft['id'],json=body)
    assert result.status_code==422,result.text


def test_source_check_does_not_claim_semantic_validation(client,draft_fixture):
    _,draft=draft_fixture;body=patch_body(draft)
    paragraph=next(section for section in body['sections'] if section['id']=='related')['paragraphs'][0]
    paragraph.update(kind='fact',text='The source must be checked for semantic support.')
    result=client.patch('/api/studio/drafts/'+draft['id'],json=body);assert result.status_code==200,result.text
    saved=next(section for section in result.json()['sections'] if section['id']=='related')['paragraphs'][0]
    assert saved['review_status']=='source_and_numbers_checked_semantics_pending'


def test_deleted_pdf_invalidates_all_draft_versions(client,draft_fixture):
    document,draft=draft_fixture
    assert client.delete('/api/documents/'+document['id']).status_code in (200,204)
    for suffix in ['', '?revision=1', '/export', '/revisions']:
        assert client.get('/api/studio/drafts/'+draft['id']+suffix).status_code==404
    assert not any(item['id']==draft['id'] for item in client.get('/api/studio/assets').json()['items'])


def test_reference_identity_needs_identifier_and_title_and_bibtex_is_escaped():
    document={'id':'fixture','work_id':'fixture','arxiv_id':'2605.12345','title':'Fixture title'}
    record={'key':'arxiv:2605.12345v1','provider':'arxiv','identifier':'2605.12345v1','title':'Wrong paper'}
    assert studio.reference_for_document(document,[record])['metadata'] is None
    record['title']='Fixture title'
    assert studio.reference_for_document(document,[record])['identity_basis']=='identifier_and_title'
    escaped=studio.bib_escape('A {title} 50% & $x_1$')
    assert r'\{title\}' in escaped and r'50\%' in escaped and r'\$x\_1\$' in escaped


def test_registry_binding_requires_matching_authors_and_keeps_actual_doi(client,draft_fixture):
    from backend.app import literature_store,store
    document,draft=draft_fixture
    record={'key':'doi:10.9999/synthetic-studio-test','provider':'crossref','identifier':'10.9999/synthetic-studio-test',
            'doi':'10.9999/synthetic-studio-test','title':document['title'],'authors':['Fixture Author'],'year':2026,
            'landing_url':'https://example.test/synthetic-registration','source_url':'https://example.test/synthetic-registry','retrieved_at':store.now()}
    literature_store.save_records([record])
    body={'document_id':document['id'],'record_key':record['key'],'expected_revision':1,'confirm_identity':True}
    wrong={**record,'key':'doi:10.9999/synthetic-other','identifier':'10.9999/synthetic-other','authors':['Different Person']}
    literature_store.save_records([wrong])
    assert client.post('/api/studio/drafts/'+draft['id']+'/reference',json={**body,'record_key':wrong['key']}).status_code==422
    result=client.post('/api/studio/drafts/'+draft['id']+'/reference',json=body)
    assert result.status_code==200,result.text
    assert result.json()['revision']==2
    bib=client.get('/api/studio/drafts/'+draft['id']+'/export?format=bibtex').text
    assert 'doi = {10.9999/synthetic-studio-test}' in bib
    assert 'Different Person' not in bib


def test_no_suitable_body_evidence_creates_editable_plan_instead_of_quotes(client,sample_pdfs):
    document=wait_for_document(client,upload_pdf(client,sample_pdfs['v1'])['id'])
    result=client.post('/api/studio/drafts',json={'idea':'synthetic software evidence','document_ids':[document['id']]})
    assert result.status_code==200,result.text
    draft=result.json();assert not draft['evidence']
    related=next(section for section in draft['sections'] if section['id']=='related')
    assert related['paragraphs'][0]['kind']=='plan'
    assert client.patch('/api/studio/drafts/'+draft['id'],json=patch_body(draft)).status_code==200


def test_line_renderer_leaves_nan_at_excluded_positions(monkeypatch,tmp_path):
    import matplotlib.axes
    original=matplotlib.axes.Axes.errorbar;observed=[]
    def capture(self,x,y,*args,**kwargs):
        observed.append(list(y));return original(self,x,y,*args,**kwargs)
    monkeypatch.setattr(matplotlib.axes.Axes,'errorbar',capture)
    dataset=runtime.parse_csv(b'step,value\n1,10\n2,\n3,20\n')
    selected=studio.ChartSpec(chart_type='line',x='step',y=['value'],x_kind='numeric',missing_policy='drop',title='Synthetic gap').model_dump()
    runtime.render_chart(runtime.prepare_chart(dataset,selected),tmp_path)
    assert len(observed)==1 and math.isnan(observed[0][1]) and observed[0][0]==10 and observed[0][2]==20


def test_long_labels_are_rejected_before_rendering():
    dataset=runtime.parse_csv(('model,value\n'+'x'*1000+',1\n').encode())
    selected=studio.ChartSpec(x='model',y=['value']).model_dump()
    with pytest.raises(ValueError,match='简短标识符'):runtime.prepare_chart(dataset,selected)
