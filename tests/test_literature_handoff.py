"""Local receipts and pinned post-import tasks, using fictional registry records."""
import copy
import io
import json
import uuid
import zipfile

import pytest

from backend.app import literature, literature_handoff as handoff, literature_store, store, workflow,studio
from conftest import wait_for_document
from test_literature import crossref, atom
from test_workflow import papers, planned, start, wait


def record_for(document):
    item=literature.parse_crossref(crossref())['items'][0]
    item.update(key='doi:10.1234/'+uuid.uuid4().hex,title=document['title'])
    literature_store.save_records([item]);return item


def local_body(item,document,**changes):
    return {'key':item['key'],'document_id':document['id'],'parse_run_id':document['parse_run_id'],
            'sha256':document['sha256'],'confirm_identity':True,**changes}


def confirmed(client,document):
    item=record_for(document)
    result=client.post('/api/literature/handoffs',json=local_body(item,document))
    assert result.status_code==200,result.text
    return item,result.json()


def saved(item,items=None):
    return literature_store.save_search({'query':item['identifier'],'provider':item['provider']},
        {'items':[item] if items is None else items,'providers':[],'warnings':[]})


def test_local_association_requires_confirmation_and_is_idempotent(client,papers):
    document=papers[0];item=record_for(document)
    assert client.post('/api/literature/handoffs',json=local_body(item,document,confirm_identity=False)).status_code==422
    route='/api/literature/local-candidates'
    candidates=client.get(route,params={'key':item['key']}).json()['items']
    assert document['id'] in {item['id'] for item in candidates}
    first=client.post('/api/literature/handoffs',json=local_body(item,document)).json()
    second=client.post('/api/literature/handoffs',json=local_body(item,document)).json()
    assert first['id']==second['id'] and first['can_plan'] and first['identity_basis']=='user_confirmed'
    assert first['semantic_verified'] is False and first['source']['parse_run_id']==document['parse_run_id']
    assert client.get('/api/literature/handoffs/'+first['id']).json()==first


@pytest.mark.parametrize('change',['title','sha','run','deleted'])
def test_wrong_or_changed_local_source_cannot_be_bound(client,papers,change):
    document=papers[0];item=record_for(document);body=local_body(item,document)
    if change=='title':body.update(document_id=papers[1]['id'],parse_run_id=papers[1]['parse_run_id'],sha256=papers[1]['sha256'])
    elif change=='sha':body['sha256']='0'*64
    elif change=='run':body['parse_run_id']='foreign-run'
    else:body['document_id']='missing'
    assert client.post('/api/literature/handoffs',json=body).status_code in {404,409,422}


def test_arxiv_candidate_must_match_exact_revision(client,papers):
    document=papers[0]
    # Fictional fixture PDFs do not contain a real arXiv identity.
    store.update_document(document['id'],arxiv_id='2605.12345',version_label='v1')
    try:
        local=store.get_document(document['id']);item=literature.parse_arxiv(atom('2605.12345v1'))['items'][0]
        assert handoff.matches(item,local)
        item['identifier']='2605.12345v999';literature_store.save_records([item])
        assert not handoff.matches(item,local)
        assert client.post('/api/literature/handoffs',json=local_body(item,local)).status_code==422
    finally:store.update_document(document['id'],arxiv_id=document['arxiv_id'],version_label=document['version_label'])


def test_search_and_parent_context_are_validated_before_download(client,papers,monkeypatch):
    document=papers[0];item=record_for(document);search_id=saved(item,items=[])
    async def forbidden(*args):raise AssertionError('invalid scope cannot download')
    monkeypatch.setattr(literature,'download_pdf',forbidden)
    assert client.post('/api/literature/import',json={'key':item['key'],'search_id':search_id,'confirm_identity':True}).status_code==422
    assert client.get('/api/literature/local-candidates',params={'key':item['key'],'search_id':search_id}).status_code==422
    parent=planned(client,papers,actions=['evidence']);good=saved(item)
    assert client.post('/api/literature/handoffs',json=local_body(item,document,search_id=good,parent_task_id=parent['id'])).status_code==422
    assert client.post('/api/literature/handoffs',json=local_body(item,document,parent_task_id=parent['id'])).status_code==422


def test_snapshot_not_latest_registry_is_carried_to_child_and_export(client,papers):
    document=papers[0];item=record_for(document);search_id=saved(item)
    parent=planned(client,papers,actions=['evidence'])
    workflow.repo.mutate(parent['id'],lambda task:task['steps'].append({'id':'discovery-fixture','tool':'discovery','result':{'search_id':search_id}}))
    parent_before=copy.deepcopy(workflow.repo.get(parent['id']))
    mutated={**item,'title':'Changed latest registry record'};literature_store.save_records([mutated])
    receipt=client.post('/api/literature/handoffs',json=local_body(item,document,search_id=search_id,parent_task_id=parent['id'])).json()
    assert receipt['metadata']['title']==item['title']
    task=planned(client,papers,goal='model architecture limitations',actions=['evidence','writing'],handoff_id=receipt['id'])
    assert task['handoff']['metadata']['title']==item['title'] and task['handoff']['parent_task_id']==parent['id']
    start(client,task);done=wait(client,task['id']);assert done['status']=='completed_with_gaps'
    writing=next(step['result'] for step in done['steps'] if step['tool']=='writing')
    reference=next(r for r in writing['references'] if r['document_id']==document['id'])
    assert reference['metadata']==task['handoff']['metadata'] and reference['identity_basis']=='confirmed_fulltext_handoff'
    assert writing['handoff']==task['handoff']
    assert workflow.repo.get(parent['id'])==parent_before
    with zipfile.ZipFile(io.BytesIO(client.get('/api/research-tasks/'+task['id']+'/export').content)) as archive:
        assert json.loads(archive.read('handoff.json'))==task['handoff'] and '全文交接来源' in archive.read('task.md').decode()
        assert item['title'] in archive.read('references.bib').decode() and 'Changed latest registry record' not in archive.read('references.bib').decode()
    assert done['usage']['model']['calls']==done['usage']['metadata']['requests']==0


@pytest.mark.parametrize('change',['metadata','document','run'])
def test_writing_rejects_forged_or_foreign_confirmed_metadata(client,papers,change):
    _,resolved=confirmed(client,papers[0]);receipt=copy.deepcopy(handoff.ready(resolved['id']))
    ids=[papers[0]['id']];runs={papers[0]['id']:papers[0]['parse_run_id']}
    if change=='metadata':receipt['metadata']['title']='FORGED'
    elif change=='document':ids=[papers[1]['id']];runs={papers[1]['id']:papers[1]['parse_run_id']}
    else:runs={papers[0]['id']:papers[1]['parse_run_id']}
    with pytest.raises(Exception) as error:studio.build_draft(studio.DraftRequest(idea='Synthetic writing plan',document_ids=ids,parse_runs=runs),handoff=receipt)
    assert getattr(error.value,'status_code',None) in {404,409}


def test_writing_reuses_only_same_confirmed_binding(client,papers):
    _,resolved=confirmed(client,papers[0]);receipt=handoff.ready(resolved['id']);identity=str(uuid.uuid4())
    body=studio.DraftRequest(idea='Synthetic writing plan',document_ids=[papers[0]['id']],parse_runs={papers[0]['id']:papers[0]['parse_run_id']})
    draft=studio.build_draft(body,artifact_id=identity,handoff=receipt)
    assert studio.build_draft(body,artifact_id=identity,handoff=receipt)==draft
    with pytest.raises(Exception) as error:studio.build_draft(body,artifact_id=identity)
    assert getattr(error.value,'status_code',None)==409


@pytest.mark.parametrize('invalid',['omit','replace_run','unknown_receipt'])
def test_invalid_handoff_plan_is_rejected_before_model(client,papers,monkeypatch,invalid):
    _,receipt=confirmed(client,papers[0])
    async def forbidden(*args,**kwargs):raise AssertionError('No model before handoff validation')
    monkeypatch.setattr(workflow.model_client,'chat_json',forbidden)
    body={'goal':'整理研究方法','document_ids':[p['id'] for p in papers],'handoff_id':receipt['id'],'planner':'model'}
    if invalid=='omit':body['document_ids']=[papers[1]['id']]
    elif invalid=='replace_run':body['parse_runs']={papers[0]['id']:'other-parse'}
    else:body['handoff_id']='absent'
    assert client.post('/api/research-tasks',json=body).status_code in {404,409,422}


def test_reprocessing_keeps_historical_run_available_to_new_child(client,papers):
    document=papers[0];_,receipt=confirmed(client,document)
    response=client.post('/api/documents/'+document['id']+'/reprocess',json={'ocr':False,'tables':False});assert response.status_code==200
    current=wait_for_document(client,document['id']);assert current['parse_run_id']!=document['parse_run_id']
    value=client.get('/api/literature/handoffs/'+receipt['id']).json();assert value['can_plan'] and '历史版本' in value['notice']
    task=planned(client,[current],goal='model architecture',actions=['evidence'],handoff_id=receipt['id'])
    assert task['sources'][0]['parse_run_id']==document['parse_run_id']
    start(client,task);done=wait(client,task['id']);assert done['status']=='completed_with_gaps'
    for step in done['steps']:
        for evidence in (step.get('result') or {}).get('evidence',[]):assert evidence['parse_run_id']==document['parse_run_id']


def test_file_changes_block_previously_planned_child_before_retrieval(client,papers):
    document=papers[0];_,receipt=confirmed(client,document)
    task=planned(client,[document],goal='model architecture',actions=['evidence'],handoff_id=receipt['id'])
    from pathlib import Path
    path=Path(store.get_internal_document(document['id'])['file_path']);raw=path.read_bytes()
    try:
        path.write_bytes(raw+b'changed fixture')
        assert not client.get('/api/literature/handoffs/'+receipt['id']).json()['can_plan']
        start(client,task);done=wait(client,task['id']);assert done['status']=='failed'
        assert all(step['result'] is None for step in done['steps']) and done['usage']['model']['calls']==0
    finally:path.write_bytes(raw)


def test_download_import_returns_receipt_and_after_parser_it_can_plan(client,sample_pdfs,monkeypatch):
    item=literature.parse_arxiv(atom('2606.12345v7'))['items'][0];literature_store.save_records([item]);calls=[]
    async def download(identifier):calls.append(identifier);return io.BytesIO(sample_pdfs['distinct'].read_bytes()),'https://arxiv.org/pdf/'+identifier
    monkeypatch.setattr(literature,'download_pdf',download)
    result=client.post('/api/literature/import',json={'key':item['key'],'confirm_identity':True});assert result.status_code==200,result.text
    receipt=result.json()['handoff'];document=wait_for_document(client,result.json()['document']['id'])
    resolved=client.get('/api/literature/handoffs/'+receipt['id']).json();assert resolved['can_plan']
    task=planned(client,[document],goal='model architecture',actions=['evidence'],handoff_id=receipt['id'])
    assert task['sources'][0]['parse_run_id']==receipt['source']['parse_run_id'] and calls==[item['identifier']]


@pytest.mark.parametrize('state',['queued','failed','missing_blocks'])
def test_incomplete_bound_run_cannot_plan(client,papers,state):
    document=papers[0];_,receipt=confirmed(client,document)
    with store.connection() as conn:
        old=conn.execute('SELECT status FROM parse_runs WHERE id=?',(document['parse_run_id'],)).fetchone()[0]
        conn.execute('UPDATE parse_runs SET status=? WHERE id=?',('queued' if state=='queued' else 'failed' if state=='failed' else 'ready',document['parse_run_id']))
    if state=='missing_blocks':
        # A ready flag without any blocks is not a usable source.
        other='parse_'+uuid.uuid4().hex
        with store.connection() as conn:conn.execute('UPDATE blocks SET parse_run_id=? WHERE parse_run_id=?',(other,document['parse_run_id']))
    try:
        assert not client.get('/api/literature/handoffs/'+receipt['id']).json()['can_plan']
        response=client.post('/api/research-tasks',json={'goal':'整理研究方法','document_ids':[document['id']],'handoff_id':receipt['id']})
        assert response.status_code==409
    finally:
        with store.connection() as conn:
            conn.execute('UPDATE parse_runs SET status=? WHERE id=?',(old,document['parse_run_id']))
            if state=='missing_blocks':conn.execute('UPDATE blocks SET parse_run_id=? WHERE parse_run_id=?',(document['parse_run_id'],other))


def test_unknown_handoff_is_404(client):assert client.get('/api/literature/handoffs/absent').status_code==404


def test_handoff_document_is_not_replaced_by_newer_same_work(client,papers):
    for index,document in enumerate(papers):store.update_document(document['id'],arxiv_id='2607.12345',version_label='v'+str(index+1))
    try:
        documents=[store.get_document(document['id']) for document in papers];_,receipt=confirmed(client,documents[0])
        task=planned(client,documents,goal='model architecture',actions=['evidence'],handoff_id=receipt['id'])
        assert len(task['sources'])==1 and task['sources'][0]['document_id']==documents[0]['id']
    finally:
        for document in papers:store.update_document(document['id'],arxiv_id=document['arxiv_id'],version_label=document['version_label'])


def test_deleted_source_receipt_stays_unusable(client,papers):
    document=papers[0];_,receipt=confirmed(client,document)
    assert client.delete('/api/documents/'+document['id']).status_code==200
    value=client.get('/api/literature/handoffs/'+receipt['id']).json()
    assert value['status']=='source_unavailable' and value['document'] is None and not value['can_plan']
    assert client.post('/api/research-tasks',json={'goal':'整理研究方法','document_ids':[document['id']],'handoff_id':receipt['id']}).status_code==409
