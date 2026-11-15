"""Fictional source tests for durable, bounded writing generation."""
import asyncio
import copy
import json
import inspect
import uuid

import pytest
from fastapi import HTTPException
from backend.app import model_client,studio,writing_generation as writing,store
from test_studio import draft_fixture,patch_body


def request_body(draft):return {'request_id':str(uuid.uuid4()),'expected_revision':draft['revision']}


def proposal_for(draft):
    source=draft['evidence'][0];quote=source['text'].split('. ')[0]+'.'
    fact={'kind':'fact','text':'该合成样例仅用于软件接口测试。','evidence_id':source['id'],'support_quote':quote}
    return {'sections':[{'id':name,'paragraphs':[copy.deepcopy(fact) if name in {'introduction','related'} else {'kind':'plan','text':'拟使用独立测试划分，核对来源并记录失败条件。'}]} for name in writing.SECTIONS]}


def stub(monkeypatch,proposal,side_effect=None):
    calls=[];monkeypatch.setattr(model_client,'configuration',lambda:{'ready':True})
    async def chat(system,payload,*,max_tokens):
        calls.append(copy.deepcopy(payload));budget=model_client._budget.get();budget.reserve(max_tokens)
        if side_effect:
            result=side_effect()
            if inspect.isawaitable(result):await result
        budget.record({'prompt_tokens':123,'completion_tokens':234})
        return copy.deepcopy(proposal)
    monkeypatch.setattr(model_client,'chat_json',chat)
    return calls


def generate(client,draft,body):return client.post('/api/studio/drafts/'+draft['id']+'/generate',json=body)


def test_generated_revision_quotes_exports_and_idempotency(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture;calls=stub(monkeypatch,proposal_for(draft));body=request_body(draft)
    response=generate(client,draft,body);assert response.status_code==200,response.text
    attempt=response.json();assert attempt['status']=='completed' and attempt['usage']['calls']==1 and attempt['usage']['output_tokens']==234
    current=client.get('/api/studio/drafts/'+draft['id']).json();assert current['revision']==2 and current['mode']=='model_grounded_plan'
    fact=current['sections'][1]['paragraphs'][0];assert fact['support_quote'] and fact['evidence_ids'] and 'semantics_pending' in fact['review_status']
    assert client.get('/api/studio/drafts/'+draft['id']+'?revision=1').json()==draft
    assert generate(client,current,body).json()==attempt and len(calls)==1
    assert calls[0]['evidence'][0]['document_id']==draft['sources'][0]['document_id']
    assert client.get('/api/studio/drafts/'+draft['id']+'/generations').json()['items'][0]['proposal']==proposal_for(draft)
    exported=client.get('/api/studio/drafts/'+draft['id']+'/export').text
    assert fact['support_quote'] in exported and '语义' in exported and '书目身份待核对' in exported
    assert generate(client,current,{**body,'instructions':'A different writing instruction'}).status_code==409


def test_quote_repair_requires_unique_match_in_same_parse_version():
    quote = 'The authors report a bounded experiment.'
    original = {'id':'a', 'document_id':'paper', 'parse_run_id':'v1', 'page':1, 'text':'Different passage.'}
    target = {**original, 'id':'b', 'page':3, 'text':quote}
    assert writing.match_fact_quote(original,quote,[original,target],repair=True)[0] == target
    for other in ({**target,'document_id':'another'}, {**target,'parse_run_id':'v2'}):
        with pytest.raises(ValueError,match='quote_not_in_source'):
            writing.match_fact_quote(original,quote,[original,other],repair=True)
    with pytest.raises(ValueError,match='quote_not_in_source'):
        writing.match_fact_quote(original,quote,[original,target,{**target,'id':'c'}],repair=True)
    with pytest.raises(ValueError,match='quote_not_in_source'):
        writing.match_fact_quote(original,quote,[original,target])


def test_writing_review_keeps_unconfirmed_paraphrase_only_in_attempt(client,draft_fixture,monkeypatch):
    _, draft = draft_fixture
    proposal = proposal_for(draft)
    calls = []
    monkeypatch.setattr(model_client,'configuration',lambda:{'ready':True})

    async def model(system, payload, *, max_tokens):
        model_client._budget.get().reserve(max_tokens)
        calls.append(payload)
        if len(calls) == 1:
            return copy.deepcopy(proposal)
        assert len(payload['claims']) == 2 and 'idea' not in payload
        checked = payload['claims'][0]
        return {'checks':[{'claim_id':checked['claim_id'],'status':'supported',
                          'reason':'Fixture support','unsupported_parts':[],
                          'quote_support':[dict(checked['quotes'][0])],
                          'scope':{'status':'aligned','claim_excerpt':checked['text'],'reason':'Fixture scope'}}]}

    monkeypatch.setattr(model_client,'chat_json',model)
    result = generate(client,draft,{**request_body(draft),'verify_facts':True}).json()
    assert result['status']=='completed' and result['usage']['calls']==2
    assert [row['status'] for row in result['fact_checks']]==['supported','insufficient']
    saved = client.get('/api/studio/drafts/'+draft['id']).json()
    assert saved['sections'][1]['paragraphs'][0]['kind']=='fact'
    related = saved['sections'][2]['paragraphs'][0]
    assert related['kind']=='quote' and related['text']==proposal['sections'][2]['paragraphs'][0]['support_quote']
    assert result['proposal']==proposal and saved['generation']['semantic_verified'] is False


@pytest.mark.parametrize('change',['foreign_evidence','invented_quote','number_outside_quote','plan_as_result','missing_source','extra_reference','wrong_section'])
def test_rejected_proposal_preserves_candidate_without_changing_draft(client,draft_fixture,monkeypatch,change):
    _,draft=draft_fixture;proposal=proposal_for(draft);fact=proposal['sections'][2]['paragraphs'][0]
    if change=='foreign_evidence':fact['evidence_id']='foreign'
    elif change=='invented_quote':fact['support_quote']='An invented source quotation, never present in the PDF.'
    elif change=='number_outside_quote':fact.update(text='训练批量为32。',support_quote='The evidence retrieval method is evaluated only as a software interface in these automated tests.')
    elif change=='plan_as_result':proposal['sections'][0]['paragraphs'][0]['text']='拟开展研究，实验表明已经提升了性能。'
    elif change=='missing_source':proposal['sections'][2]['paragraphs']=[{'kind':'plan','text':'拟核对已有工作。'}]
    elif change=='extra_reference':proposal['references']=[{'doi':'fictional'}]
    else:proposal['sections'][0]['id']='foreign_section'
    calls=stub(monkeypatch,proposal);body=request_body(draft);attempt=generate(client,draft,body).json()
    assert attempt['status']=='rejected' and attempt['proposal']==proposal and attempt['semantic_verified'] is False
    assert client.get('/api/studio/drafts/'+draft['id']).json()==draft
    assert generate(client,draft,body).json()==attempt and len(calls)==1


def test_concurrent_edit_is_not_overwritten_and_attempt_is_retained(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture
    def edit():
        body=patch_body(draft);body['title']='Concurrent manual revision'
        assert studio.update_draft(draft['id'],studio.DraftPatch(**body))['revision']==2
    calls=stub(monkeypatch,proposal_for(draft),edit);attempt=generate(client,draft,request_body(draft)).json()
    assert attempt['status']=='conflict' and attempt['proposal'] and len(calls)==1
    current=client.get('/api/studio/drafts/'+draft['id']).json();assert current['revision']==2 and current['title']=='Concurrent manual revision'


def test_stale_revision_and_missing_configuration_prevent_call(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture;calls=stub(monkeypatch,proposal_for(draft))
    assert generate(client,draft,{**request_body(draft),'expected_revision':2}).status_code==409
    monkeypatch.setattr(model_client,'configuration',lambda:{'ready':False})
    assert generate(client,draft,request_body(draft)).status_code==409 and not calls


def test_changed_file_and_source_deleted_during_call_cannot_publish(client,draft_fixture,monkeypatch):
    document,draft=draft_fixture;calls=stub(monkeypatch,proposal_for(draft))
    path=store.get_internal_document(document['id'])['file_path']
    from pathlib import Path
    original=Path(path).read_bytes()
    try:
        Path(path).write_bytes(original+b' modified')
        assert generate(client,draft,request_body(draft)).status_code==409 and not calls
    finally:Path(path).write_bytes(original)
    stub(monkeypatch,proposal_for(draft),lambda:store.delete_document(document['id']))
    attempt=generate(client,draft,request_body(draft)).json();assert attempt['status']=='rejected' and attempt['proposal']
    assert client.get('/api/studio/drafts/'+draft['id']).status_code==404


def test_inflight_duplicate_is_not_repeated_and_restart_does_not_retry(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture;body=request_body(draft);seen=[]
    async def duplicate():
        seen.append(await writing.generate(draft,writing.GenerateRequest(**body)))
        with pytest.raises(HTTPException) as error:await writing.generate(draft,writing.GenerateRequest(**request_body(draft)))
        assert error.value.status_code==409
    calls=stub(monkeypatch,proposal_for(draft),duplicate);attempt=generate(client,draft,body).json()
    assert seen[0]['status']=='running' and len(calls)==1 and attempt['status']=='completed'
    # A process can stop with a reserved request and no parsed candidate.
    interrupted=copy.deepcopy(attempt);interrupted.update(id=str(uuid.uuid4()),status='running',proposal=None)
    with store.connection() as conn:conn.execute('INSERT INTO writing_generations VALUES(?,?,?,?)',(interrupted['id'],draft['id'],json.dumps(interrupted),store.now()))
    writing.recover();assert writing.get(interrupted['id'])['status']=='interrupted' and len(calls)==1


def test_atomic_save_failure_rolls_back_revision(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture;stub(monkeypatch,proposal_for(draft))
    original=writing.write_attempt
    def fail(attempt,conn=None):
        if conn is not None and attempt['status']=='completed':raise ValueError('synthetic_commit_failure')
        return original(attempt,conn)
    monkeypatch.setattr(writing,'write_attempt',fail)
    attempt=generate(client,draft,request_body(draft)).json();assert attempt['status']=='rejected' and 'result_revision' not in attempt
    assert client.get('/api/studio/drafts/'+draft['id']).json()==draft


def test_model_failure_is_recorded_and_same_request_cannot_retry(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture
    def fail():raise ValueError('synthetic_failure')
    calls=stub(monkeypatch,proposal_for(draft),fail);body=request_body(draft)
    attempt=generate(client,draft,body).json();assert attempt['status']=='failed' and attempt['usage']['calls']==1
    assert generate(client,draft,body).json()==attempt and len(calls)==1


def test_saved_rejected_candidate_can_be_corrected_locally_without_rewriting_it(client,draft_fixture,monkeypatch):
    _,draft=draft_fixture;bad=proposal_for(draft);bad['sections'][2]['paragraphs'][0]['support_quote']='A fabricated quotation absent from the original source.'
    calls=stub(monkeypatch,bad);attempt=generate(client,draft,request_body(draft)).json();assert attempt['status']=='rejected'
    path=f"/api/studio/drafts/{draft['id']}/generations/{attempt['id']}/review"
    body={'expected_revision':1,'proposal':proposal_for(draft),'review_note':'Synthetic source alignment correction; no semantic certification.'}
    assert client.post(path,json={**body,'proposal':bad}).status_code==422
    response=client.post(path,json=body);assert response.status_code==200,response.text
    assert response.json()['revision']==2 and response.json()['generation']['id']==attempt['id'] and response.json()['mode']=='reviewed_model_plan'
    saved=writing.get(attempt['id']);assert saved['proposal']==bad and saved['status']=='rejected' and saved['reviews'][0]['model_calls']==0
    assert client.post(path,json=body).status_code==409 and len(calls)==1
    other=client.post('/api/studio/drafts',json={'idea':'Another synthetic writing plan','document_ids':[draft['sources'][0]['document_id']]}).json()
    assert client.post(f"/api/studio/drafts/{other['id']}/generations/{attempt['id']}/review",json=body).status_code==404
