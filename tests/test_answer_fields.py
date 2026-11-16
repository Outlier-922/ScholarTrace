"""Synthetic contracts and failure cases, not model quality scores."""
import asyncio
import pytest
from backend.app import qa,answer_fields,model_client

QUESTION='比较两篇的 GPU 数量、型号和训练耗时。'


def source(identity,document,text):
    return {'id':identity,'document_id':document,'parse_run_id':'original_'+document,'block_id':'block_'+identity,
            'title':'Synthetic '+document,'page':2,'bbox':[.1,.2,.8,.3],'text':text,'score':1}


@pytest.fixture
def evidence():
    return [source('ea','a','The fictional training used 7 Z77 GPUs for 9 days.'),
            source('eb','b','The fictional training used 11 N22 GPUs for 13 days.')]


def row(document,field,text='',ids=(),status='answered'):
    return {'document_id':document,'field':field,'text':text,'evidence_ids':list(ids),'status':status}


def interpret(rows,evidence,question=QUESTION):
    return answer_fields.interpret({'field_answers':rows},question,evidence,qa._validate_claims)


def test_only_explicit_subfields_are_requested():
    assert answer_fields.requested(QUESTION)==['gpu_count','gpu_model','duration']
    assert answer_fields.requested('GPU核心时钟频率是多少MHz？')==['gpu_clock']
    assert answer_fields.requested('GPU运行电压是多少伏特？')==['gpu_voltage']
    assert 'gpu_memory' not in answer_fields.requested(QUESTION)


def test_component_question_is_independent_from_architecture():
    question='比较模型架构，两篇模型是否都包含用于生成的解码器？'
    assert set(answer_fields.requested(question))=={'architecture','component_presence'}
    assert 'component_presence' not in answer_fields.requested('模型是否准确？')
    assert answer_fields.requested('是否保留pooler？未提及不等于移除了。')==['component_presence']
    result=interpret([], [source('e','a','An encoder.')], question)
    assert any(c['field']=='component_presence' and c['status']=='not_assessed' for c in result['answer_coverage'])


@pytest.mark.parametrize('verdict,quote,accepted',[
    ('absent','The model uses an encoder.',False),
    ('absent','The model has no decoder.',False),
    ('absent','The model uses no decoder.',True),
    ('present','',True),
    (None,'',False),
    (['absent'],'',False),
    ({'status':'absent'},'',False),
])
def test_presence_requires_verdict_and_explicit_attributed_absence(verdict,quote,accepted):
    statement='The model uses a decoder.' if verdict=='present' else 'The model uses no decoder.'
    evidence=[source('e','a',statement)]
    item={**row('a','component_presence',statement,['e']),
          'presence_status':verdict,'support_quote':quote}
    result=interpret([item],evidence,'是否包含解码器？')
    assert bool(result['claims'])==accepted
    assert result['field_validation']['semantic_verified'] is False


def test_unreported_component_remains_missing_instead_of_absent():
    result=interpret([row('a','component_presence',status='missing')],
                     [source('e','a','The model uses an encoder.')],'是否包含解码器？')
    assert not result['claims'] and result['answer_coverage'][0]['status']=='missing_in_evidence'


def test_absence_quote_accepts_only_whitespace_normalization():
    evidence=[source('e','a','The model uses\nno decoder.')]
    item={**row('a','component_presence','The model uses no decoder.',['e']),
          'presence_status':'absent','support_quote':'The model uses no decoder.'}
    assert interpret([item],evidence,'是否包含解码器？')['claims']
    item['support_quote']='The model never uses a decoder.'
    assert not interpret([item],evidence,'是否包含解码器？')['claims']


@pytest.mark.parametrize('question,expected',[
    ('模型有多少层，输出维度 d_model 是多少，多头注意力有多少个头？',{'layers','hidden_size','attention_heads'}),
    ('比较模型参数量与context window',{'parameter_count','sequence_length'}),
    ('编码器包含多少层，big模型在WMT英语到德语的BLEU是多少？',{'layers','bleu'}),
    ('模型的hidden size和number of parameters是多少？',{'hidden_size','parameter_count'}),
])
def test_multi_part_questions_keep_each_requested_quantity(question,expected):
    assert set(answer_fields.requested(question))==expected


def test_new_fields_are_not_silently_dropped_from_missing_coverage(evidence):
    result=interpret([row('a','layers','7 layers',['ea'])],evidence,'模型有多少层，输出维度是多少，注意力头数是多少？')
    assert {item['field'] for item in result['answer_coverage']}=={'layers','hidden_size','attention_heads'}
    assert any('隐藏/输出维度' in item for item in result['missing_information'])


def test_unrelated_score_clause_does_not_dominate_layer_probe():
    question='编码器有多少层？big模型在WMT2014上的BLEU是多少？'
    query=answer_fields.probe_query(question,'layers')
    assert 'BLEU' not in query and 'WMT2014' not in query and 'big' not in query
    assert 'BLEU' in answer_fields.probe_query(question,'bleu')


def test_layernorm_question_keeps_position_stability_and_warmup():
    question='比较原始模型与On Layer Normalization in the Transformer Architecture，Pre-LN和Post-LN的位置、训练稳定性和warmup分别如何？'
    assert set(answer_fields.requested(question))=={'normalization_position','training_stability','warmup'}
    assert set(answer_fields.requested('比较模型架构和LayerNorm位置'))=={'architecture','normalization_position'}


def test_normalization_conditions_have_independent_missing_coverage(evidence):
    question='LayerNorm的位置、训练稳定性和warmup分别如何？'
    result=interpret([],evidence,question)
    assert len(result['answer_coverage'])==6
    assert all(item['status']=='not_assessed' for item in result['answer_coverage'])


@pytest.mark.parametrize('question',[
    'GPU核心时钟频率是多少MHz？只回答论文明确报告的值，不从型号推断。',
    'What core clock frequency in MHz is reported? Do not infer from the GPU model.',
])
def test_negated_constraint_is_not_an_extra_requested_field(question):
    assert answer_fields.requested(question)==['gpu_clock']


def test_do_not_only_does_not_remove_a_positive_field():
    assert 'gpu_count' in answer_fields.requested('不要只比较GPU数量，还要比较训练耗时。')


def test_probe_preserves_section_and_variant_without_paper_specific_answer():
    query=answer_fields.probe_query('比较第4.2节的模型 R17x3 训练耗时。','duration')
    assert 'Section 4.2' in query and 'R17x3' in query and 'duration' in query


def test_scoped_field_answers_keep_document_and_field(evidence):
    result=interpret([row('a','gpu_count','7 GPUs',['ea']),row('b','gpu_count','11 GPUs',['eb'])],evidence)
    assert [(claim['document_id'],claim['field']) for claim in result['claims']]==[('a','gpu_count'),('b','gpu_count')]
    assert len(result['answer_coverage'])==6 and result['field_validation']['semantic_verified'] is False


@pytest.mark.parametrize('bad',[row('a','gpu_count','11 GPUs',['eb']),row('a','gpu_count','999 GPUs',['ea']),
                                 row('a','gpu_count','7 GPUs',['invented'])])
def test_wrong_paper_or_novel_value_is_not_a_candidate_answer(evidence,bad):
    result=interpret([bad],evidence)
    assert not result['claims'] and result['answer_coverage'][0]['status']=='needs_review'


def test_unrequested_precision_is_not_added_as_missing(evidence):
    result=interpret([row('a','gpu_count','7 GPUs',['ea']),row('a','exact_voltage',status='missing')],evidence)
    assert result['field_validation']['rejected_rows']==1
    assert all('voltage' not in cell['field'] for cell in result['answer_coverage'])
    assert not any('exact_voltage' in item for item in result['missing_information'])


def test_answer_missing_conflict_is_exposed_not_hidden(evidence):
    result=interpret([row('a','gpu_count','7 GPUs',['ea']),row('a','gpu_count',status='missing')],evidence)
    assert result['claims'] and result['answer_coverage'][0]['status']=='needs_review'


def test_duplicate_different_answers_require_review(evidence):
    result=interpret([row('a','gpu_count','7 GPUs',['ea']),row('a','gpu_count','9 GPUs',['ea'])],evidence)
    assert result['answer_coverage'][0]['status']=='needs_review'


def test_missing_and_not_assessed_are_distinct(evidence):
    result=interpret([row('a','gpu_count',status='missing')],evidence)
    assert result['answer_coverage'][0]['status']=='missing_in_evidence'
    assert result['answer_coverage'][1]['status']=='not_assessed'
    assert all('整篇没有' not in item for item in result['missing_information'])


@pytest.mark.parametrize('text',['L = ^{S}','Loss =','x^{}','x^{4'])
def test_obvious_formula_fragment_rejected(text):
    assert not qa._validate_claims({'claims':[{'text':text,'evidence_ids':['e']}]},[{'id':'e','text':text}])


def test_well_formed_exponent_not_rejected():
    text='n = 2^{7}'
    assert qa._validate_claims({'claims':[{'text':text,'evidence_ids':['e']}]},[{'id':'e','text':text}])


def test_bad_rows_do_not_prevent_other_answers(evidence):
    result=interpret([{'document_id':[],'field':[]},'invalid',row('a','gpu_count','7 GPUs',['ea'])],evidence)
    assert len(result['claims'])==1 and result['field_validation']['rejected_rows']==2


def test_model_call_remains_single_and_cannot_forge_server_coverage(monkeypatch,evidence):
    calls=[]
    async def generated(system,payload,**kwargs):
        calls.append(payload);assert kwargs['max_tokens']==1600
        assert [field['key'] for field in payload['requested_fields']]==['gpu_count','gpu_model','duration']
        return {'field_answers':[row('a','gpu_count','7 GPUs',['ea'])],'answer_coverage':[{'status':'fully_verified'}]}
    monkeypatch.setattr(model_client,'chat_json',generated)
    result=asyncio.run(qa._generate(QUESTION,evidence))
    assert len(calls)==1 and all(cell['status']!='fully_verified' for cell in result['answer_coverage'])


def test_legacy_answer_works_without_trusting_provider_coverage(monkeypatch,evidence):
    async def generated(*args,**kwargs):
        return {'claims':[{'text':'7 GPUs','evidence_ids':['ea']}],'answer_coverage':[{'status':'fully_verified'}]}
    monkeypatch.setattr(model_client,'chat_json',generated)
    result=asyncio.run(qa._generate(QUESTION,evidence))
    assert result['claims'] and 'answer_coverage' not in result


def test_field_search_supplements_both_papers_and_respects_k(monkeypatch):
    documents=[{'id':key,'_retrieval_run':'original_'+key} for key in ['a','b']];calls=[]
    def retrieve(question,docs,limit,details=None):
        calls.append((question,[doc['id'] for doc in docs]))
        if details is not None:details.update(used=False)
        assert all(doc['_retrieval_run']=='original_'+doc['id'] for doc in docs)
        if len(docs)>1:return [source('intro_'+doc['id'],doc['id'],'Synthetic architecture background only.') for doc in docs]
        return [source('target_'+docs[0]['id'],docs[0]['id'],'The fictional training used 7 Z77 GPUs for 9 days.')]
    monkeypatch.setattr(qa,'_retrieve',retrieve)
    def forbidden(*args,**kwargs):raise AssertionError('Retrieval must not call provider')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    details={};hits=qa._retrieve_question(QUESTION,documents,2,details)
    assert len(hits)==2 and {hit['block_id'] for hit in hits}=={'block_target_a','block_target_b'}
    assert details['extra_queries_used']==6 and details['coverage']=={'a':1,'b':1}


def test_supplement_queries_have_a_hard_bound(monkeypatch):
    documents=[{'id':str(i),'_retrieval_run':'r'} for i in range(7)]
    calls=[]
    def retrieve(question,docs,limit,details=None):
        calls.append(question)
        return [source(doc['id'],doc['id'],'The fictional training used 7 Z77 GPUs for 9 days.') for doc in docs]
    monkeypatch.setattr(qa,'_retrieve',retrieve);details={}
    hits=qa._retrieve_question(QUESTION,documents,4,details)
    assert len(calls)==13 and details['extra_queries_used']==12 and details['field_queries_truncated']
    assert len(hits)==4 and sum(details['coverage'].values())==4


def test_deleted_source_invalidates_field_coverage(monkeypatch,evidence):
    result={'claims':[{'text':'7 GPUs','evidence_ids':['ea']}],'evidence':evidence,'warnings':[],
            'answer_coverage':[{'document_id':'a','status':'has_candidate_answer','evidence_ids':['ea']}]}
    monkeypatch.setattr(qa.store,'get_document',lambda identity:None if identity=='a' else {'id':'b'})
    qa._filter_deleted_sources(result,['a','b'])
    assert not result['claims'] and result['answer_coverage'][0]['status']=='source_unavailable'


def test_direct_normalization_evidence_survives_background_fill(monkeypatch):
    documents=[{'id':'a','_retrieval_run':'original_a'}]
    background=[source('intro'+str(i),'a','Architecture and normalization overview.') for i in range(6)]
    direct=source('order','a','A residual connection is followed by layer normalization.')
    def retrieve(question,docs,limit,details=None):
        if details is not None:details.update(used=False)
        return [direct,*background[:2]] if 'sublayer' in question else background
    monkeypatch.setattr(qa,'_retrieve',retrieve)
    monkeypatch.setattr(qa,'_collect_sources',lambda docs:[])
    hits=qa._retrieve_question('比较LayerNorm位置与warmup。',documents,3)
    assert len(hits)==3 and 'order' in {hit['id'] for hit in hits}


def test_named_reference_model_field_stays_in_scope_when_one_paper_has_no_hits():
    question = 'Alpha 微调时冻结哪些参数？Beta Policy Tuning 的参考模型如何使用？分别说明训练目标。'
    docs = [{'id': 'a', 'title': 'Alpha: Model Adaptation'}, {'id': 'b', 'title': 'Beta Policy Tuning: A Study'}]
    evidence = [{**source('ea', 'a', 'The base weights are frozen.'), 'title': docs[0]['title']}]
    requests = answer_fields.requested_items(question, docs)
    assert '冻结哪些参数' in requests['a', 'parameter_updates']
    assert '参考模型如何使用' in requests['b', 'reference_policy']
    proposal = {'field_answers': [
        row('a', 'parameter_updates', 'The base weights are frozen.', ['ea']),
        row('a', 'reference_policy', 'The base model is the reference.', ['ea']),
        row('b', 'reference_policy', 'The base model is the reference.', ['ea']),
    ]}
    result = answer_fields.interpret(proposal, question, evidence, qa._validate_claims, documents=docs)
    coverage = {(cell['document_id'], cell['field']): cell['status'] for cell in result['answer_coverage']}
    assert coverage == {('a', 'objective'): 'not_assessed', ('a', 'parameter_updates'): 'has_candidate_answer',
                        ('b', 'objective'): 'not_assessed', ('b', 'reference_policy'): 'needs_review'}
    assert len(result['claims']) == 1 and result['field_validation']['rejected_rows'] == 2


def test_reference_evidence_retains_loss_calculation_beyond_baseline_description(monkeypatch):
    docs = [{'id': 'a', 'title': 'Synthetic Study'}]
    baseline = source('baseline', 'a', 'The reference policy is initialized to the initial SFT model.')
    input_only = source('input', 'a', 'The reference model log probabilities are provided as input.')
    generic = source('background', 'a', 'The reference model log ratios are used in related work.')
    usage = source('usage', 'a', 'losses = -F.logsigmoid(beta * (pi_logratios - ref_logratios))')
    monkeypatch.setattr(qa, '_retrieve', lambda *args: [baseline, input_only, generic, usage])
    monkeypatch.setattr(qa, '_collect_sources', lambda docs: [])
    hits = qa._retrieve_question('参考模型如何参与计算？', docs, 2)
    assert 'usage' in {hit['id'] for hit in hits}
