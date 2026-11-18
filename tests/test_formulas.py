"""Formula software guards on existing synthetic PDF fixtures; not math accuracy."""
from hashlib import sha256
import uuid
import pymupdf
import pytest
from conftest import upload_pdf,wait_for_document
from backend.app import formulas,formula_native,formula_ocr,model_client,store


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


def scan_region(client, source):
    doc, body, _ = source
    region_body = {key: value for key,value in body.items() if key!='block_ids'}
    region_body['region']=[.18,.18,.7,.25]
    response=client.post('/api/formula-sources',json=region_body)
    assert response.status_code==200,response.text
    return region_body,response.json()


def test_region_ocr_has_distinct_anchors_and_cache(client,source,monkeypatch):
    body, value = scan_region(client,source)
    doc,_,_=source
    params={key:body[key] for key in ('page','parse_run_id')}
    original=client.get('/api/documents/'+doc['id']+'/formula-page-image',params=params)
    assert original.status_code==200 and original.content.startswith(b'\x89PNG')
    assert value['blocks']==[] and not value['native_supported'] and value['region']==body['region']
    assert client.post('/api/formula-sources',json=body).json()['id']==value['id']
    assert client.post('/api/formula-sources',json={**body,'region':[0,0,1,1]}).status_code==422
    url='/api/formula-sources/'+value['id']
    assert client.post(url+'/transcribe',json={}).status_code==422
    seen=[]
    monkeypatch.setattr(formula_ocr,'runtime_identity',lambda:'test-runtime')

    def recognize(png):
        seen.append(png)
        assert png.startswith(b'\x89PNG') and png!=original.content
        return r'\frac{x_1}{\sqrt{d}}'

    monkeypatch.setattr(formula_ocr,'recognize',recognize)
    result=client.post(url+'/recognize-scan',json={})
    assert result.status_code==200,result.text
    revision=result.json()
    assert revision['origin']=='scan_ocr_candidate' and revision['region']==value['region']
    assert revision['image_sha256']==value['image_sha256'] and revision['semantic_verified'] is False
    assert client.post(url+'/recognize-scan',json={}).json()['id']==revision['id'] and len(seen)==1
    revised=client.post(url+'/revisions',json={'latex':'x','note':'合成校订','parent_id':revision['id']}).json()
    exported=client.get('/api/formula-revisions/'+revised['id']+'/export')
    assert 'Region:' in exported.text and value['image_sha256'] in exported.text


def test_region_ocr_unavailable_or_modified_crop_does_not_generate(client,source,monkeypatch):
    _,value=scan_region(client,source)
    url='/api/formula-sources/'+value['id']
    monkeypatch.setattr(formula_ocr,'runtime_identity',lambda:None)
    assert client.post(url+'/recognize-scan',json={}).status_code==503
    formulas.repo.image_path(value['id']).write_bytes(b'modified')
    monkeypatch.setattr(formula_ocr,'runtime_identity',lambda:'test-runtime')
    assert client.post(url+'/recognize-scan',json={}).status_code==409
    assert client.get(url+'/revisions').json()['items']==[]


def test_invalid_scan_recognition_preserves_crop_for_correction(client, source, monkeypatch):
    _, value = scan_region(client, source)
    url = '/api/formula-sources/' + value['id']
    monkeypatch.setattr(formula_ocr, 'runtime_identity', lambda: 'test-runtime')
    monkeypatch.setattr(formula_ocr, 'recognize', lambda png: r'\frac{x}{')
    response = client.post(url + '/recognize-scan', json={})
    assert response.status_code == 502 and '未通过 LaTeX 检查' in response.json()['detail']
    assert client.get(url + '/image').status_code == 200
    assert client.get(url + '/revisions').json()['items'] == []


def test_cross_page_formula_keeps_both_crops_and_exports_their_provenance(client, tmp_path, monkeypatch):
    pdf = tmp_path / 'cross-page-formula.pdf'
    with pymupdf.open() as file:
        for text, y in [('u = x +', 690), ('y + z', 125), ('An unrelated formula v = 1', 125)]:
            page = file.new_page(width=600, height=800)
            page.insert_text((120, y), text, fontsize=20)
        file.save(pdf)
    doc = wait_for_document(client, upload_pdf(client, pdf)['id'])
    body = {'document_id': doc['id'], 'parse_run_id': doc['parse_run_id'], 'page': 1,
            'region': [.16,.82,.58,.89], 'continuation': [.16,.11,.58,.19]}
    path = '/api/formula-sources'
    assert client.post(path, json={**body, 'page': 3}).status_code == 422
    assert client.post(path, json={**body, 'continuation': [0,0,1,1]}).status_code == 422
    assert client.post(path, json={**body, 'region': None, 'block_ids': ['test']}).status_code == 422
    saved = client.post(path, json=body)
    assert saved.status_code == 200, saved.text
    source = saved.json()
    identity = source['id']
    url = f'{path}/{identity}'
    continued = source['continuation']
    assert (source['page'], continued['page'], continued['region']) == (1, 2, body['continuation'])
    assert client.post(path, json=body).json()['id'] == identity
    assert client.post(path, json={**body, 'continuation': None}).json()['id'] != identity
    for part in (source, continued):
        crop = client.get(url + '/image', params={'page': part['page']})
        assert crop.status_code == 200 and sha256(crop.content).hexdigest() == part['image_sha256']
        history = client.get(f"/api/documents/{doc['id']}/formula-sources", params={
            'parse_run_id': body['parse_run_id'], 'page': part['page']}).json()['items']
        assert any(row['id'] == identity and row['continuation_page'] == 2 for row in history)
    assert source['image_sha256'] != continued['image_sha256']
    assert client.get(url + '/image', params={'page': 3}).status_code == 404

    def no_guess(_):
        raise AssertionError('Do not generate LaTeX from just the first crop')

    monkeypatch.setattr(formula_ocr, 'recognize', no_guess)
    assert client.post(url+'/recognize-scan', json={}).status_code == 422
    assert source['native_supported'] and {glyph['page'] for glyph in source['glyphs']} == {1, 2}
    monkeypatch.setattr(formulas.model_client, 'configuration', lambda: {'ready': True})

    async def transcribe_both(system, data, **_):
        assert '先读第一页选区' in system
        assert {glyph['page'] for glyph in data['glyphs']} == {1, 2}
        assert not any(glyph['char'] == '1' for glyph in data['glyphs'])
        return {'status':'candidate', 'latex':'u=x+y+z', 'uncertainties':[]}

    monkeypatch.setattr(formulas.model_client, 'chat_json', transcribe_both)
    candidate = client.post(url+'/transcribe', json={})
    assert candidate.is_success and candidate.json()['continuation'] == continued
    entry = {'latex': 'u=x+y+z', 'note': 'Checked each page'}
    revision = client.post(url+'/revisions', json=entry).json()
    assert revision['continuation'] == continued and revision['origin'] == 'user_edited'
    export = '/api/formula-revisions/'+revision['id']+'/export'
    text = client.get(export).text
    assert all(f"PDF page: {part['page']}" in text and part['image_sha256'] in text for part in (source, continued))
    assert text.count('Region:') == 2
    secondary_image = formulas.repo.image_path(identity, continued=True)
    secondary_image.write_bytes(b'invalid')
    assert client.post(url+'/transcribe', json={}).status_code == 409
    assert client.get(url+'/image', params={'page':2}).status_code == 409
    assert client.get(export).status_code == 409
    assert client.post(url+'/revisions', json=entry).status_code == 409
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert not secondary_image.exists() and not formulas.repo.image_path(identity).exists()


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


def test_native_layout_reads_both_fraction_branches_and_script_baselines():
    from backend.app.formula_typesetting import recover

    def run(text, x, right, baseline, size=10, font='CMMI10'):
        return {'text':text,'x':x,'right':right,'baseline':baseline,'size':size,'font':font}

    first = {'horizontal_rules':[[2,10,24]],'runs':[
        run('x',3,7,6,7), run('n',7,10,4,5), run('i',7,10,8,5),
        run('α',9,14,16,7), run('0',14,17,17,5,'CMR5'),
        run('=',28,34,12,font='CMR10'),
    ]}
    following = {'runs':[run('z',1,5,10)]}
    assert recover([first,following]) == r'\frac{x^{n}_{i}}{\alpha _{0}}= z'
    assert recover([first,{'runs':[run('?',1,5,10,font='UnknownFont')]}]) is None


def test_native_hash_mismatch_rejected(source):
    doc,_,value=source
    internal=store.get_internal_document(doc['id'])
    with pytest.raises(ValueError,match='hash_changed'):
        formula_native.extract(internal['file_path'],'wrong',value['page'],[b['bbox'] for b in value['blocks']])
