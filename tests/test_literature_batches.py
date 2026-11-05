"""Bounded queue software checks with fictional identities and injected HTTP."""
import asyncio
import copy
import io
import uuid
import pytest
from fastapi import HTTPException
from backend.app import literature, literature_batches as batches, literature_handoff as handoff, literature_store as repo, store
from conftest import wait_for_document
from test_literature import atom
from test_workflow import papers, planned


@pytest.fixture
def source(client,papers):
    originals=copy.deepcopy(papers);records=[]
    for index,doc in enumerate(papers):
        root='2611.'+str(int(uuid.uuid4().hex[:8],16)%100000).zfill(5)
        store.update_document(doc['id'],arxiv_id=root,version_label='v1')
        records.append(literature.parse_arxiv(atom(root+'v1',title=doc['title']))['items'][0])
    repo.save_records(records)
    identity=repo.save_search({'query':'SYNTHETIC batch'}, {'items':records,'providers':[],'warnings':[]})
    try:yield identity,records,[store.get_document(d['id']) for d in papers]
    finally:
        for doc in originals:store.update_document(doc['id'],arxiv_id=doc['arxiv_id'],version_label=doc['version_label'])


def preview(client,source):
    identity,records,_=source
    result=client.post('/api/literature/batches',json={'search_id':identity,'keys':[r['key'] for r in records]})
    assert result.status_code==200,result.text
    return result.json()


def confirm(client,value,source,action='local'):
    result=client.post(f"/api/literature/batches/{value['id']}/confirm",json={'revision':value['revision'],'confirm_identity':True,
        'choices':[{'key':record['key'],'action':action,'document_id':doc['id'] if action=='local' else None} for record,doc in zip(source[1],source[2])]})
    assert result.status_code==200,result.text
    return result.json()


def advance(client,value):
    result=client.post(f"/api/literature/batches/{value['id']}/advance",json={'revision':value['revision']})
    assert result.status_code==200,result.text
    return result.json()


def test_preview_and_confirmation_do_not_download_and_pinned_local_queue_resumes(client,source,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Local queue cannot download')
    monkeypatch.setattr(literature,'download_pdf',forbidden)
    initial=preview(client,source);assert initial['status']=='draft'
    assert client.post(f"/api/literature/batches/{initial['id']}/advance",json={'revision':0}).status_code==409
    queue=confirm(client,initial,source)
    first=advance(client,queue)
    assert first['items'][0]['status']=='ready' and first['items'][1]['status']=='queued'
    assert client.get('/api/literature/batches/'+first['id']).json()==first
    final=advance(client,first)
    assert final['status']=='completed' and final['download_item_attempts']==0
    assert [i['attempts'] for i in final['items']]==[1,1]
    assert advance(client,final)==final
    for item,doc in zip(final['items'],source[2]):
        assert item['handoff']['source']['parse_run_id']==doc['parse_run_id']
    assert client.get('/api/literature/batches/'+final['id']+'/export').json()==final
    assert final['id'] in {i['id'] for i in client.get('/api/literature/batches').json()['items']}


@pytest.mark.parametrize('mutation',['unconfirmed','missing_choice','duplicate_choice','foreign_document','download_with_document'])
def test_confirmation_rejects_invalid_choices_without_partial_binding(client,source,mutation):
    value=preview(client,source)
    body={'revision':0,'confirm_identity':True,'choices':[{'key':r['key'],'action':'local','document_id':d['id']} for r,d in zip(source[1],source[2])]}
    if mutation=='unconfirmed':body['confirm_identity']=False
    elif mutation=='missing_choice':body['choices'].pop()
    elif mutation=='duplicate_choice':body['choices'][1]=body['choices'][0]
    elif mutation=='foreign_document':body['choices'][1]['document_id']='foreign'
    elif mutation=='download_with_document':body['choices'][1]['action']='download'
    before=len(repo.list_handoffs())
    assert client.post(f"/api/literature/batches/{value['id']}/confirm",json=body).status_code==422
    assert len(repo.list_handoffs())==before and not repo.batch_get(value['id'])['confirmed']


@pytest.mark.parametrize('keys',[[],['missing'],['x']*6,['x','x'],['x'*251]])
def test_bad_or_excessive_selection_never_creates_queue(client,source,keys):
    before=len(repo.batch_list())
    assert client.post('/api/literature/batches',json={'search_id':source[0],'keys':keys}).status_code in {404,422}
    assert len(repo.batch_list())==before


def test_parent_context_checked_and_snapshot_not_latest_metadata_is_used(client,source,papers):
    search_id,records,_=source;parent=planned(client,papers,actions=['evidence'])
    body={'search_id':search_id,'parent_task_id':parent['id'],'keys':[r['key'] for r in records]}
    assert client.post('/api/literature/batches',json=body).status_code==422
    from backend.app import workflow_store
    workflow_store.mutate(parent['id'],lambda t:t['steps'].append({'tool':'discovery','result':{'search_id':search_id}}))
    parent_before=workflow_store.get(parent['id'])
    repo.save_records([{**records[0],'title':'Changed latest registry title'}])
    value=client.post('/api/literature/batches',json=body).json()
    assert value['items'][0]['metadata']['title']==records[0]['title']
    done=advance(client,confirm(client,value,source))
    assert done['items'][0]['handoff']['metadata']['title']==records[0]['title']
    assert workflow_store.get(parent['id'])==parent_before


def test_failure_isolated_no_automatic_retry_and_attempt_limit_is_enforced(client,source,monkeypatch):
    calls=[]
    async def fail(*args,**kwargs):calls.append(1);raise HTTPException(502,'SYNTHETIC upstream failure')
    monkeypatch.setattr(literature,'import_paper',fail)
    queue=confirm(client,preview(client,source),source,'download')
    first=advance(client,queue);assert first['items'][0]['status']=='failed'
    assert first['items'][1]['status']=='queued'
    second=advance(client,first);assert second['status']=='blocked' and len(calls)==2
    assert advance(client,second)==second and len(calls)==2
    response=client.post(f"/api/literature/batches/{second['id']}/retry",json={'revision':second['revision'],'key':source[1][0]['key']})
    retried=advance(client,response.json());assert len(calls)==3
    assert client.post(f"/api/literature/batches/{retried['id']}/retry",json={'revision':retried['revision'],'key':source[1][0]['key']}).status_code==409
    skipped=client.post(f"/api/literature/batches/{retried['id']}/skip",json={'revision':retried['revision'],'key':source[1][0]['key']}).json()
    assert skipped['items'][0]['status']=='skipped' and len(skipped['items'][0]['history'])==2


def test_duplicate_revision_cannot_advance_next_paper(client,source,monkeypatch):
    queue=confirm(client,preview(client,source),source,'download');calls=[]
    receipts=[handoff.confirm_local(handoff.LocalRequest(key=r['key'],search_id=source[0],document_id=d['id'],parse_run_id=d['parse_run_id'],sha256=d['sha256'],confirm_identity=True)) for r,d in zip(source[1],source[2])]
    async def delayed(*args,**kwargs):calls.append(1);await asyncio.sleep(.03);return {'handoff':receipts[0]}
    monkeypatch.setattr(literature,'import_paper',delayed)
    async def run():
        return await asyncio.gather(batches.advance(queue['id'],batches.VersionRequest(revision=queue['revision']),None),batches.advance(queue['id'],batches.VersionRequest(revision=queue['revision']),None),return_exceptions=True)
    result=asyncio.run(run())
    assert len(calls)==1 and sum(isinstance(r,HTTPException) and r.status_code==409 for r in result)==1
    assert repo.batch_get(queue['id'])['items'][1]['attempts']==0


def test_real_upload_path_with_injected_pdf_keeps_downloaded_handoff(client,source,sample_pdfs,monkeypatch):
    calls=[]
    async def injected(identifier):calls.append(identifier);return io.BytesIO(sample_pdfs['v1'].read_bytes()),'https://arxiv.org/pdf/'+identifier
    monkeypatch.setattr(literature,'download_pdf',injected)
    queue=confirm(client,preview(client,source),source,'download');first=advance(client,queue)
    item=first['items'][0]
    assert item['status'] in {'ready','waiting_parse'} and calls==[source[1][0]['identifier']]
    wait_for_document(client,item['handoff']['source']['id'])
    current=client.get('/api/literature/batches/'+queue['id']).json()
    assert current['items'][0]['handoff']['origin']=='downloaded_pdf'
    assert current['items'][0]['handoff']['source']['sha256']==source[2][0]['sha256']


def test_process_restart_requires_explicit_retry_and_recovers_saved_receipt_without_network(client,source,monkeypatch):
    queue=confirm(client,preview(client,source),source,'download')
    saved=repo.batch_get(queue['id']);item=saved['items'][0]
    item.update(status='running',owner='SYNTHETIC prior process',attempts=1,history=[{'attempt':1,'started_at':store.now()}]);batches.save(saved)
    receipt=handoff.capture(batches.context(saved,item['key']),item['metadata'],source[2][0],'downloaded_pdf')
    assert client.get('/api/literature/batches/'+queue['id']).json()['items'][0]['status']=='interrupted'
    async def forbidden(*args,**kwargs):raise AssertionError('Recover saved receipt, not another download')
    monkeypatch.setattr(literature,'import_paper',forbidden)
    retry=client.post(f"/api/literature/batches/{queue['id']}/retry",json={'revision':saved['revision'],'key':item['key']}).json()
    result=advance(client,retry)
    assert result['items'][0]['handoff_id']==receipt['id'] and result['items'][0]['history'][-1]['recovered_receipt']


def test_changed_local_parse_blocks_confirm_and_deletion_invalidates_ready_item(client,source):
    previewed=preview(client,source);doc=source[2][0]
    assert client.post('/api/documents/'+doc['id']+'/reprocess',json={'ocr':False,'tables':False}).status_code==200
    wait_for_document(client,doc['id'])
    body={'revision':0,'confirm_identity':True,'choices':[{'key':r['key'],'action':'local','document_id':d['id']} for r,d in zip(source[1],source[2])]}
    assert client.post(f"/api/literature/batches/{previewed['id']}/confirm",json=body).status_code==409


def test_source_unavailable_does_not_redownload_and_skip_preserves_error(client,source,monkeypatch):
    queue=confirm(client,preview(client,source),source);first=advance(client,queue)
    store.delete_document(source[2][0]['id'])
    current=client.get('/api/literature/batches/'+queue['id']).json()
    assert current['items'][0]['status']=='source_unavailable' and not current['items'][0]['handoff']['can_plan']
    response=client.post(f"/api/literature/batches/{queue['id']}/skip",json={'revision':current['revision'],'key':source[1][0]['key']})
    final=advance(client,response.json())
    assert final['status']=='completed_with_gaps' and final['items'][0]['handoff_id']
    assert final['items'][0]['skipped_from']=='source_unavailable' and final['items'][0]['error']


def test_cancelled_request_keeps_attempt_record_and_does_not_continue(client,source,monkeypatch):
    queue=confirm(client,preview(client,source),source,'download')
    async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
    monkeypatch.setattr(literature,'import_paper',cancelled)
    with pytest.raises(asyncio.CancelledError):asyncio.run(batches.advance(queue['id'],batches.VersionRequest(revision=queue['revision']),None))
    value=repo.batch_get(queue['id'])
    assert value['items'][0]['status']=='failed' and value['items'][0]['history'][0]['outcome']=='interrupted'
    assert value['items'][1]['attempts']==0
