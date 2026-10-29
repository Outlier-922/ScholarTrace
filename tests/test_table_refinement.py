"""Native-grid source integrity and intentionally conservative table edits."""
import csv
import io
import json
import pytest
from conftest import upload_pdf,wait_for_document
from test_tables import grid_pdf
from backend.app import store,table_refine_native as native,table_refine_store as repo


@pytest.fixture
def region(client,grid_pdf):
    doc=wait_for_document(client,upload_pdf(client,grid_pdf)['id'])
    body={'document_id':doc['id'],'parse_run_id':doc['parse_run_id'],'page':1,'bbox':[.1,.125,5/6,.25625],'header_bottom':.16875}
    response=client.post('/api/table-regions',json=body);assert response.status_code==200,response.text
    try:yield doc,body,response.json()
    finally:client.delete('/api/documents/'+doc['id']);repo.purge_document(doc['id'])


def test_actual_native_region_preserves_rows_numbers_blanks_and_word_refs(client,region):
    doc,body,source=region;g=source['grid']
    assert [c['label'] for c in g['columns']]==['Model','Accuracy (%)','Latency (ms)']
    assert g['rows']==[['Synthetic Alpha','72.5','12.0'],['Synthetic Beta','74.0',None]]
    assert not g['unassigned'] and source['semantic_verified'] is False
    assert client.post('/api/table-regions',json=body).json()['id']==source['id']
    assert client.get('/api/table-regions/'+source['id']+'/image').content.startswith(b'\x89PNG')
    referenced={w for c in g['cells'] for w in c['word_ids']}|{w for c in g['columns'] for w in c['word_ids']}
    assert referenced=={w['id'] for w in source['words']}
    assert client.get('/api/documents/'+doc['id']+'/table-regions',params={'page':1,'parse_run_id':body['parse_run_id']}).json()['items'][0]['id']==source['id']


def test_immutable_manual_changes_exports_and_parent_scope(client,region):
    _,_,s=region;url='/api/table-regions/'+s['id'];rows=[r[:] for r in s['grid']['rows']];rows[0][1]='73.0'
    response=client.post(url+'/revisions',json={'rows':rows,'labels':['Model','SYNTHETIC revised metric','Latency (ms)'],'note':'SYNTHETIC correction, not a measurement'})
    assert response.status_code==200,response.text
    r=response.json();assert r['semantic_verified'] is False and r['changed_cells']==[{'row':0,'column':1,'before':'72.5','after':'73.0'}]
    assert client.get(url).json()['grid']['rows'][0][1]=='72.5'
    bundle=client.get(url+'/export',params={'format':'json','revision_id':r['id']}).json()
    assert bundle['source']['sha256']==s['sha256'] and bundle['revision']['rows'][0][1]=='73.0'
    exported=client.get(url+'/export',params={'format':'csv','revision_id':r['id']})
    parsed=list(csv.reader(io.StringIO(exported.text.lstrip('\ufeff'))));assert parsed[1][1]=='73.0' and parsed[2][2]==''
    assert len(client.get(url+'/revisions').json()['items'])==1
    assert client.post(url+'/revisions',json={'rows':rows,'labels':['a','b','c'],'note':'test','parent_id':s['id']}).status_code==422


@pytest.mark.parametrize('change',[{'page':999},{'page':True},{'bbox':[-.1,.1,.8,.5]},{'bbox':[.5,.1,.4,.5]},{'header_bottom':.9},{'column_cuts':[.7,.3]},{'column_cuts':[.05,.4]},{'anchor_block_id':'foreign'},{'bbox':[0,.1,1]},{'bbox':[0,.1,1,1],'header_bottom':'NaN'}])
def test_invalid_or_foreign_geometry_is_rejected(client,region,change):
    _,body,_=region
    assert client.post('/api/table-regions',json={**body,**change}).status_code==422


@pytest.mark.parametrize('change',[{'rows':[['only one']]},{'labels':['a','b']},{'labels':['','b','c']},{'note':' '},{'rows':[['X'*1001,'a','b'],['c','d','e']]}])
def test_manual_invalid_shape_or_size_rejected(client,region,change):
    _,_,s=region
    body={'rows':s['grid']['rows'],'labels':[c['label'] for c in s['grid']['columns']],'note':'synthetic'}
    assert client.post('/api/table-regions/'+s['id']+'/revisions',json={**body,**change}).status_code==422


def test_explicit_column_cuts_are_recorded_not_hidden_rules(client,region):
    _,body,_=region
    s=client.post('/api/table-regions',json={**body,'column_cuts':[.4,.6]}).json()
    assert s['grid']['cuts']==[.1,.4,.6,5/6] and s['requested_column_cuts']==[.4,.6]


def test_old_parse_source_survives_and_delete_invalidates_crop(client,region):
    doc,_,s=region;path=repo.image_path(s['id']);assert path.exists()
    store.start_reprocess(doc['id'],{'ocr':False,'tables':False})
    assert client.get('/api/table-regions/'+s['id']).status_code==200
    assert client.delete('/api/documents/'+doc['id']).status_code==200
    assert not path.exists() and client.get('/api/table-regions/'+s['id']).status_code==404


def test_native_hash_mismatch_fails(region):
    doc,body,_=region
    with pytest.raises(ValueError,match='hash_changed'):native.extract(store.get_internal_document(doc['id'])['file_path'],'wrong',1,body['bbox'],body['header_bottom'],{},None)


def test_empty_native_region_does_not_invent_scanned_cells():
    g=native.build([], [0,0,1,1],.2,{},None)
    assert g['status']=='manual_only' and g['rows']==[]


def word(name,text,x,y,width=.05):return {'id':name,'text':text,'layout_text':text,'bbox':[x,y,x+width,y+.02]}


def test_crossing_word_keeps_entire_row_unassigned():
    words=[word('h1','Model',.1,.1),word('h2','Metric',.6,.1),word('b1','Text-crossing-cut',.2,.3,.5),word('b2','42',.8,.3)]
    g=native.build(words,[.05,.08,.95,.5],.2,{},[.05,.5,.95])
    assert g['rows']==[[None,None]] and g['unassigned'][0]['word_ids']==['b1','b2']


def test_legacy_vertical_group_retained_without_value_propagation():
    words=[word('h1','Group',.1,.1),word('h2','Metric',.6,.1),word('group','A',.1,.35),word('v1','1',.6,.3),word('v2','2',.6,.4)]
    legacy={'cells':[{'row':1,'column':0,'text':'A','bbox':[.05,.25,.5,.48]}]}
    g=native.build(words,[.05,.08,.95,.5],.2,legacy,[.05,.5,.95])
    assert g['rows']==[['A','1'],[None,'2']]
    assert g['cells'][0]['row_span']==2 and g['cells'][2]['covered_by']==[0,0]


def test_math_product_is_not_split_into_two_metric_cells():
    words=[word('h1','A',.1,.1),word('h2','B',.6,.1),word('b1','2.3',.40,.3,.04),word('dot','·',.46,.3,.015),word('power','10^{19}',.49,.3,.08)]
    g=native.build(words,[.05,.08,.95,.5],.2,{},[.05,.48,.95])
    assert g['rows']==[[None,None]] and g['unassigned']


def test_parent_header_above_two_leaf_columns_is_retained_in_both_paths():
    words=[word('parent','Score',.45,.1,.1),word('left','A',.2,.15),word('right','B',.7,.15),word('one','1',.2,.3),word('two','2',.7,.3)]
    g=native.build(words,[.05,.08,.95,.5],.2,{},[.05,.5,.95])
    assert [c['label'] for c in g['columns']]==['Score / A','Score / B']


def test_unattached_legacy_group_label_is_kept_as_unassigned_text():
    words=[word('h1','Group',.1,.1),word('h2','Metric',.6,.1),word('group','A',.1,.35),word('v1','1',.6,.3)]
    legacy={'cells':[{'row':1,'column':0,'text':'A','bbox':[.05,.25,.5,.48]}]}
    g=native.build(words,[.05,.08,.95,.5],.2,legacy,[.05,.5,.95])
    assert g['unassigned'][0]['word_ids']==['group'] and g['rows']==[[None,'1']]


def test_native_word_limit_precedes_span_materialization(region,monkeypatch):
    import pymupdf
    import hashlib
    from pathlib import Path
    doc,body,_=region;path=Path(store.get_internal_document(doc['id'])['file_path'])
    def fake(self,kind,**kwargs):
        assert kind=='words','Over-limit source must stop before span extraction'
        return [None]*2001
    monkeypatch.setattr(pymupdf.Page,'get_text',fake)
    with pytest.raises(ValueError,match='word_limit'):native.extract(path,hashlib.sha256(path.read_bytes()).hexdigest(),1,body['bbox'],body['header_bottom'],{},None)


def test_engine_failure_never_reports_empty_success(client,region,monkeypatch):
    _,body,_=region
    from concurrent.futures import Future
    class FailedExecutor:
        def submit(self,*args):
            future=Future();future.set_exception(RuntimeError('SYNTHETIC engine failure'));return future
    monkeypatch.setattr(client.app.state,'parse_executor',FailedExecutor())
    # Different geometry bypasses the intentionally cached original.
    assert client.post('/api/table-regions',json={**body,'header_bottom':.17}).status_code==422


@pytest.mark.parametrize('value,expected',[('=SUM(A1)','\'=SUM(A1)'),('+cmd','\'+cmd'),(' @formula','\' @formula'),('-2.5','-2.5'),('-cmd','\'-cmd'),('72.5','72.5')])
def test_csv_does_not_export_executable_formulas(value,expected):
    from backend.app.table_refinement import safe_csv
    assert safe_csv(value)==expected
