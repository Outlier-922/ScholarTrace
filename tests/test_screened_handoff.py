import copy,io,json,zipfile
import pytest
from test_reference_screening import candidates,scope,graph_papers
from test_workflow import papers,planned,start,wait
from backend.app import literature_handoff as handoff,literature_store as repo,store,studio

@pytest.fixture
def bound(client,candidates,papers):
    value,ctx,body=candidates
    screened=client.post('/api/literature/searches/'+value['search_id']+'/screen',json=body).json()
    doc=papers[1];store.update_document(doc['id'],arxiv_id='2605.22222',version_label='v1')
    body={'key':screened['items'][0]['key'],'search_id':screened['search_id'],'document_id':doc['id'],'sha256':doc['sha256'],'parse_run_id':doc['parse_run_id'],'confirm_identity':True}
    try:
        result=client.post('/api/literature/handoffs',json=body)
        assert result.status_code==200,result.text
        yield screened,ctx,store.get_document(doc['id']),result.json(),body
    finally:store.update_document(doc['id'],arxiv_id=doc['arxiv_id'],version_label=doc['version_label'])

def test_screening_carries_into_new_task_writing_and_export_without_becoming_target_evidence(client,bound):
    screened,ctx,doc,receipt,body=bound
    assert receipt['screening']['goal']==screened['screening']['goal']
    assert receipt['screening']['decision']==screened['screening']['decisions'][0]
    assert client.post('/api/literature/handoffs',json=body).json()['id']==receipt['id']
    task=planned(client,[doc],goal='SYNTHETIC method evidence and writing plan',actions=['evidence','writing'],handoff_id=receipt['id'],max_model_calls=0)
    assert task['handoff']['screening']==receipt['screening'] and len(task['sources'])==1
    start(client,task);done=wait(client,task['id']);assert done['status']=='completed_with_gaps'
    draft=next(s['result'] for s in done['steps'] if s['tool']=='writing')
    assert draft['handoff']['screening']==receipt['screening']
    assert all(e['document_id']==doc['id'] for e in draft['evidence'])
    markdown=studio.writing_exports(draft)[0]
    assert '采用依据（源论文的引用用途，非候选论文事实证据）' in markdown
    assert receipt['screening']['decision']['anchor']['quote'] in markdown.replace('\n> ','\n')
    with zipfile.ZipFile(io.BytesIO(client.get('/api/research-tasks/'+task['id']+'/export').content)) as archive:
        assert json.loads(archive.read('handoff.json'))['screening']==receipt['screening']
    assert repo.get_search(screened['search_id'])['result']==screened

def test_source_deletion_invalidates_screened_receipt_before_planning(client,bound):
    screened,ctx,doc,receipt,body=bound
    task=planned(client,[doc],goal='SYNTHETIC evidence',actions=['evidence'],handoff_id=receipt['id'],max_model_calls=0)
    store.delete_document(ctx['document_id'])
    current=client.get('/api/literature/handoffs/'+receipt['id']).json()
    assert current['status']=='screening_unavailable' and not current['can_plan']
    response=client.post('/api/research-tasks',json={'goal':'SYNTHETIC evidence','document_ids':[doc['id']],'handoff_id':receipt['id']})
    assert response.status_code==409
    assert client.post('/api/literature/handoffs',json=body).status_code==404
    start(client,task);failed=wait(client,task['id']);assert failed['status']=='failed' and failed['usage']['model']['calls']==0

def test_changed_saved_screening_does_not_silently_change_existing_receipt(client,bound):
    screened,ctx,doc,receipt,body=bound
    saved=repo.get_search(screened['search_id'])
    try:
        changed=copy.deepcopy(saved['result']);changed['screening']['goal']='SYNTHETIC changed goal'
        with store.connection() as conn:conn.execute('UPDATE literature_searches SET payload=? WHERE id=?',(json.dumps(changed),screened['search_id']))
        assert not client.get('/api/literature/handoffs/'+receipt['id']).json()['can_plan']
    finally:
        with store.connection() as conn:conn.execute('UPDATE literature_searches SET payload=? WHERE id=?',(json.dumps(saved['result']),screened['search_id']))

def test_forged_screening_snapshot_rejected_by_existing_validation(client,bound):
    screened,ctx,doc,receipt,body=bound
    raw=copy.deepcopy(handoff.ready(receipt['id']));raw['screening']['decision']['reason']='SYNTHETIC forged'
    with pytest.raises(Exception) as error:handoff.validate_snapshot(raw)
    assert error.value.status_code==409
