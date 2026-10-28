"""Formula software guards on existing synthetic PDF fixtures; not math accuracy."""
import uuid
import pytest
from conftest import upload_pdf,wait_for_document
from backend.app import formulas,formula_native,model_client,store


@pytest.fixture
def source(client,sample_pdfs,monkeypatch,tmp_path):
    path=tmp_path/'SYNTHETIC-formula-fixture.pdf'
    path.write_bytes(sample_pdfs['distinct'].read_bytes()+b'\n% SYNTHETIC formula fixture '+uuid.uuid4().hex.encode())
    doc=wait_for_document(client,upload_pdf(client,path)['id'])
    structure=client.get('/api/documents/'+doc['id']+'/structure').json()
    block=next(b for b in structure['blocks'] if b['type']=='paragraph' and len(b['text'])<500)
    body={'document_id':doc['id'],'parse_run_id':doc['parse_run_id'],'page':block['page'],'block_ids':[block['id']]}
    response=client.post('/api/formula-sources',json=body)
    assert response.status_code==200,response.text
    for key,val in {'LLM_BASE_URL':'https://synthetic.example/v1','LLM_MODEL':'synthetic','LLM_API_KEY':'synthetic-only'}.items():monkeypatch.setenv(key,val)
    try:yield doc,body,response.json()
    finally:
        client.delete('/api/documents/'+doc['id'])
        formulas.repo.purge_document(doc['id'])


def test_source_crop_glyphs_cache_and_metadata(client,source):
    doc,body,value=source
    assert value['native_supported'] and value['glyphs'] and len(value['glyphs'])<=800
    image=client.get('/api/formula-sources/'+value['id']+'/image')
    assert image.status_code==200 and image.content.startswith(b'\x89PNG')
    assert client.post('/api/formula-sources',json=body).json()['id']==value['id']
    assert client.get(f"/api/documents/{doc['id']}/formula-sources",params={'page':body['page'],'parse_run_id':body['parse_run_id']}).json()['items']


def test_model_candidate_is_unverified_cached_and_manually_versioned(client,source,monkeypatch):
    _,_,value=source;calls=[]
    async def fake(system,data,**kwargs):
        assert set(data)=={'blocks','glyphs'} and kwargs['max_tokens']==1600
        calls.append(1);return {'status':'candidate','latex':r'y = x^{a}','uncertainties':['SYNTHETIC: exponent requires original-page review.']}
    monkeypatch.setattr(model_client,'chat_json',fake)
    url='/api/formula-sources/'+value['id']
    first=client.post(url+'/transcribe',json={}).json()
    assert first['latex']==r'y = x^{a}' and first['semantic_verified'] is False
    assert client.post(url+'/transcribe',json={}).json()['id']==first['id'] and len(calls)==1
    revised=client.post(url+'/revisions',json={'latex':r'y = x_{a}','note':'SYNTHETIC manual correction','parent_id':first['id']}).json()
    assert revised['id']!=first['id'] and revised['origin']=='user_edited' and revised['semantic_verified'] is False
    history=client.get(url+'/revisions').json()['items'];assert len(history)==2
    exported=client.get('/api/formula-revisions/'+revised['id']+'/export')
    assert exported.status_code==200 and 'SHA256:' in exported.text and 'ParseRun:' in exported.text


@pytest.mark.parametrize('tex',[r'\input{secret}',r'\href{https://example.test}{x}',r'\def\a{\a}\a',r'\htmlClass{x}{y}',r'\includegraphics{x}',r'\frac{a}{b',r'\begin{matrix}x\end{cases}',r'\begin{document}x\end{document}','$x$','x%comment'])
def test_unsupported_tex_is_rejected(tex):
    with pytest.raises(ValueError):formulas.validate_tex(tex)


@pytest.mark.parametrize('condition',['number','invalid_tex','malformed_notes','insufficient'])
def test_bad_model_formula_is_never_saved(client,source,monkeypatch,condition):
    _,_,value=source
    async def fake(*args,**kwargs):
        return {'status':'insufficient' if condition=='insufficient' else 'candidate','latex':r'\input{x}' if condition=='invalid_tex' else 'x=999999999' if condition=='number' else 'y=x','uncertainties':{} if condition=='malformed_notes' else []}
    monkeypatch.setattr(model_client,'chat_json',fake)
    response=client.post('/api/formula-sources/'+value['id']+'/transcribe',json={})
    assert response.status_code==(422 if condition=='insufficient' else 502)
    assert not client.get('/api/formula-sources/'+value['id']+'/revisions').json()['items']


def test_scanned_or_ocr_source_cannot_reach_text_model(client,source,monkeypatch):
    import json
    _,body,value=source
    with store.connection() as c:
        block=value['blocks'][0]
        c.execute('UPDATE blocks SET metadata=? WHERE id=?',(json.dumps({**block['metadata'],'extraction':'rapidocr'}),block['id']))
    value=client.post('/api/formula-sources',json=body).json()
    assert value['native_supported'] is False
    async def forbidden(*args,**kwargs):raise AssertionError('No text-model guess for image-only source')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    assert client.post('/api/formula-sources/'+value['id']+'/transcribe',json={}).status_code==422
    assert client.post('/api/formula-sources/'+value['id']+'/revisions',json={'latex':'y=x','note':'SYNTHETIC hand entry'}).status_code==200


def test_scope_and_parent_mismatch_rejected(client,source):
    doc,body,value=source
    assert client.post('/api/formula-sources',json={**body,'block_ids':['foreign']}).status_code==422
    assert client.post('/api/formula-sources',json={**body,'page':999}).status_code==422
    assert client.post('/api/formula-sources/'+value['id']+'/revisions',json={'latex':'x','note':'test','parent_id':value['id']}).status_code==422


def test_old_parse_sources_survive_reparse_and_deletion_purges_image(client,source):
    doc,_,value=source
    image=formulas.repo.image_path(value['id']);assert image.exists()
    store.start_reprocess(doc['id'],{'ocr':False,'tables':False})
    assert client.get('/api/formula-sources/'+value['id']).status_code==200
    assert client.delete('/api/documents/'+doc['id']).status_code==200
    assert client.get('/api/formula-sources/'+value['id']+'/image').status_code==404 and not image.exists()


def test_source_deleted_during_model_call_is_not_saved(client,source,monkeypatch):
    doc,_,value=source
    async def fake(*args,**kwargs):
        store.delete_document(doc['id']);return {'status':'candidate','latex':'y=x','uncertainties':[]}
    monkeypatch.setattr(model_client,'chat_json',fake)
    assert client.post('/api/formula-sources/'+value['id']+'/transcribe',json={}).status_code==404


@pytest.mark.parametrize('failure,status',[('budget',429),('provider',502),('configuration',503)])
def test_model_failure_leaves_source_available(client,source,monkeypatch,failure,status):
    _,_,value=source
    async def fake(*args,**kwargs):raise ValueError('model_budget_exhausted' if failure=='budget' else 'bad_output')
    monkeypatch.setattr(model_client,'chat_json',fake)
    if failure=='configuration':monkeypatch.setenv('LLM_API_KEY','')
    assert client.post('/api/formula-sources/'+value['id']+'/transcribe',json={}).status_code==status
    assert client.get('/api/formula-sources/'+value['id']).status_code==200


def test_native_hash_mismatch_rejected(source):
    doc,_,value=source
    internal=store.get_internal_document(doc['id'])
    with pytest.raises(ValueError,match='hash_changed'):
        formula_native.extract(internal['file_path'],'wrong',value['page'],[b['bbox'] for b in value['blocks']])
