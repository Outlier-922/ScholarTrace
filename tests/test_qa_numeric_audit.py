"""Source-literal guards and candidate provenance, not semantic-quality scores."""
import asyncio
import copy
import pytest
from backend.app import qa,answer_fields,model_client

EVIDENCE=[{'id':'e','document_id':'d','title':'SYNTHETIC','page':1,'text':'SYNTHETIC: We used 7 Z77 GPUs and 65,536 tokens, with rate 1e-4.'}]

@pytest.mark.parametrize('text',['使用999张GPU。','The model is Z999.','536 tokens','batch为65536','rate为4','精度为0.9'])
def test_missing_numeric_literals_cannot_hide_behind_han_or_model_labels(text):
    detail={}
    assert not qa._validate_claims({'claims':[{'text':text,'evidence_ids':['e']}]},EVIDENCE,detail)
    decision=detail['decisions'][0]
    assert decision['reason']=='novel_numeric_literal'
    for item in decision['unknown_numbers']:assert text[slice(*item['span'])]==item['literal']

@pytest.mark.parametrize('text',['使用7张GPU。','The model is Z77.','65,536 tokens','rate为1E-4'])
def test_actual_literals_still_work_with_han_identifiers_and_scientific_notation(text):
    assert qa._validate_claims({'claims':[{'text':text,'evidence_ids':['e']}]},EVIDENCE)

def test_same_numbers_do_not_prove_conditions_or_units():
    # Deliberately records the limit of a literal guard, never a semantic pass.
    assert qa._validate_claims({'claims':[{'text':'7 days','evidence_ids':['e']}]},EVIDENCE)

def test_every_legacy_decision_retains_its_original_index():
    rows=['invalid',{'text':'','evidence_ids':['e']},{'text':'L =','evidence_ids':['e']},
          {'text':'7','evidence_ids':['unknown']},{'text':'7','evidence_ids':['e']}]
    d={};qa._validate_claims({'claims':rows},EVIDENCE,d)
    assert [x['index'] for x in d['decisions']]==list(range(5))
    assert [x['reason'] for x in d['decisions']]==['invalid_claim','invalid_claim_text','formula_fragment','invalid_evidence_ids','literal_checks_passed']

def test_field_rejection_keeps_original_proposal_and_number_reason(monkeypatch):
    proposal={'field_answers':[{'document_id':'d','field':'gpu_count','status':'answered','text':'使用999张GPU。','evidence_ids':['e']}]}
    async def model(*args,**kwargs):return copy.deepcopy(proposal)
    monkeypatch.setattr(model_client,'chat_json',model)
    result=asyncio.run(qa._generate('GPU数量是多少？',EVIDENCE))
    assert result['model_proposal']==proposal and not result['claims']
    assert result['field_validation']['decisions'][0]['reason']=='novel_numeric_literal'
    assert result['answer_coverage'][0]['status']=='needs_review'

def test_unrequested_or_cross_document_rows_are_not_facts(monkeypatch):
    rows=[{'document_id':'x','field':'gpu_count','status':'answered','text':'7','evidence_ids':['e']},
          {'document_id':'d','field':'duration','status':'answered','text':'7','evidence_ids':['e']},
          {'document_id':'d','field':'gpu_count','status':'answered','text':'7','evidence_ids':['unknown']}]
    async def model(*args,**kwargs):return {'field_answers':rows,'answer_coverage':[{'status':'fully_verified'}]}
    monkeypatch.setattr(model_client,'chat_json',model)
    result=asyncio.run(qa._generate('GPU数量是多少？',EVIDENCE))
    assert result['model_proposal']['field_answers']==rows and not result['claims']
    assert result['field_validation']['rejected_rows']==3
    assert [x['reason'] for x in result['field_validation']['decisions']]==['invalid_document_or_field','invalid_document_or_field','invalid_status_or_document_evidence']

def test_more_than_twenty_candidates_remain_in_original_but_are_not_checked(monkeypatch):
    rows=[{'text':'7 GPUs','evidence_ids':['e']} for _ in range(23)]
    async def model(*args,**kwargs):return {'claims':rows}
    monkeypatch.setattr(model_client,'chat_json',model)
    result=asyncio.run(qa._generate('请说明实验。',EVIDENCE))
    assert len(result['model_proposal']['claims'])==23 and len(result['claims'])==20
    assert result['claim_validation']['truncated']==3

def test_deleted_source_hides_raw_rejected_proposals_too(monkeypatch):
    result={'claims':[],'evidence':copy.deepcopy(EVIDENCE),'warnings':[],
            'model_proposal':{'claims':[{'text':'PRIVATE_DELETED_SOURCE'}]},'claim_validation':{'decisions':[1]},'field_validation':{'decisions':[1]}}
    monkeypatch.setattr(qa.store,'get_document',lambda _:None)
    qa._filter_deleted_sources(result,['d'])
    assert not result['evidence'] and 'model_proposal' not in result and 'claim_validation' not in result
    assert 'decisions' not in result['field_validation']

def test_missing_assessment_is_not_a_rejected_claim():
    row={'document_id':'d','field':'gpu_count','status':'missing','text':'','evidence_ids':[]}
    result=answer_fields.interpret({'field_answers':[row]},'GPU数量是多少？',EVIDENCE,qa._validate_claims)
    assert result['field_validation']['decisions'][0]['status']=='missing_candidate'
    assert result['field_validation']['rejected_rows']==0


def test_http_history_preserves_rejected_original_and_hides_it_after_delete(client,sample_pdfs,monkeypatch):
    from conftest import upload_pdf,wait_for_document
    doc=wait_for_document(client,upload_pdf(client,sample_pdfs['distinct'])['id'])
    for key,value in {'LLM_BASE_URL':'https://synthetic.example/v1','LLM_API_KEY':'synthetic-not-a-secret','LLM_MODEL':'synthetic'}.items():monkeypatch.setenv(key,value)
    async def model(system,payload,**kwargs):
        return {'claims':[{'text':'使用999张GPU。','evidence_ids':[payload['evidence'][0]['id']]}]}
    monkeypatch.setattr(model_client,'chat_json',model)
    response=client.post('/api/ask',json={'question':'How many records are in the retrieval index?','document_ids':[doc['id']]})
    assert response.status_code==200
    result=response.json();assert not result['claims']
    assert result['model_proposal']['claims'][0]['text']=='使用999张GPU。'
    assert result['claim_validation']['decisions'][0]['reason']=='novel_numeric_literal'
    assert client.get('/api/agent-runs/'+result['run_id']).json()['model_proposal']==result['model_proposal']
    def forbidden(*args,**kwargs):raise AssertionError('Revalidation must not call a model')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    replay=client.post('/api/agent-runs/'+result['run_id']+'/revalidate').json()
    assert replay['run_id']!=result['run_id'] and replay['model_proposal']==result['model_proposal']
    assert replay['validation_replay']['actual_model_calls']==0
    assert client.get('/api/agent-runs/'+result['run_id']).json()['model_proposal']==result['model_proposal']
    assert client.delete('/api/documents/'+doc['id']).status_code in [200,204]
    hidden=client.get('/api/agent-runs/'+result['run_id']).json()
    assert 'model_proposal' not in hidden and 'claim_validation' not in hidden
    assert client.post('/api/agent-runs/'+result['run_id']+'/revalidate').status_code==409


@pytest.mark.parametrize('factor',[3,17])
def test_reported_factor_allows_only_a_witnessed_unit_fraction(factor):
    evidence=[{'id':'ratio','text':f'The fictional width is reduced by a factor of {factor}.'}]
    text=f'宽度减少为原来的1/{factor}（reduced by a factor of {factor}）。'
    details={};assert qa._validate_claims({'claims':[{'text':text,'evidence_ids':['ratio']}]},evidence,details)
    witness=details['decisions'][0]['numeric_transforms'][0]
    assert witness['denominator']==str(factor) and evidence[0]['text'][slice(*witness['source_quote_range'])]==witness['source_quote']
    assert text[slice(*witness['numerator_span'])]=='1' and witness['semantic_verified'] is False


@pytest.mark.parametrize('original,text',[
    ('The width is reduced by a factor of 3.','宽度减少为原来的1/4（reduced by a factor of 3）。'),
    ('The width is reduced by a factor of 3.','宽度减少为原来的1/3。'),
    ('The width is reduced by a factor of 3.','宽度减少了1/3（reduced by a factor of 3）。'),
    ('The width is not\nreduced by a factor of 3.','宽度减少为原来的1/3（reduced by a factor of 3）。'),
    ('The width is reduced by a factor of 3.5 and there are 3 groups.','宽度减少为原来的1/3（reduced by a factor of 3）。'),
    ('The width is reduced by a factor of 3.','宽度减少为原来的1/3，并训练999天（reduced by a factor of 3）。'),
])
def test_no_unwitnessed_fraction_extra_literal_or_general_arithmetic(original,text):
    assert not qa._validate_claims({'claims':[{'text':text,'evidence_ids':['ratio']}]},[{'id':'ratio','text':original}])
