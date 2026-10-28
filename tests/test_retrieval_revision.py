"""Generic synthetic regression cases for multilingual scope and PDF layout."""
import hashlib
import threading
import time
import pymupdf
import pytest
from conftest import upload_pdf, wait_for_document
from backend.app import qa, parser, semantic_jobs


def block(identity,text,path=()):
    return {'id':identity,'text':text,'page':1,'bbox':[.1,.1,.8,.2],'type':'paragraph','section_path':list(path)}


def setup_sources(monkeypatch,blocks):
    monkeypatch.setattr(qa.store,'get_structure',lambda identity,**kwargs:{'parse_run_id':'run', 'blocks':blocks[identity]})
    monkeypatch.setattr(qa.semantic,'search',lambda *a,**k:([],{'used':False}))


def test_chinese_vocabulary_question_finds_english_passage(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('target','The vocabulary has 37000 wordpieces and is shared by encoder and decoder.'),
                                             block('other','The model improves performance in the final stage of training.')]})
    result=qa._retrieve('词表是否共享？',[{'id':'a','title':'Synthetic fixture'}],1)
    assert result[0]['block_id']=='target'


@pytest.mark.parametrize('question',['第4.2节基线的训练步数是多少？','What training steps are used in Section 4.2?'])
def test_section_path_outranks_incidental_body_section_mention(monkeypatch,question):
    setup_sources(monkeypatch,{'a':[block('noise','Section 4.2 training steps baseline training training steps.', ['6. More experiments']),
                                   block('target','We train for 730 steps in this baseline experiment.', ['4. Experiments','4.2 Baseline','4.2.1 Training'])]})
    assert qa._retrieve(question,[{'id':'a','title':'Synthetic fixture'}],1)[0]['block_id']=='target'


def test_multidocument_balanced_slots_and_independent_dense_status(monkeypatch):
    setup_sources(monkeypatch,{d:[block(d+str(i),'vocabulary training settings token batch size '+str(i)) for i in range(8)] for d in ['a','b']})
    calls=[]
    def search(question,sources,**kwargs):
        scope={source['document_id'] for source in sources};calls.append(scope)
        if scope=={'a'}:return [(s['id'],.9) for s in sources],{'used':True}
        return [],{'used':False,'index_required':[{'document_id':'b','parse_run_id':'run','missing_chunks':8}]}
    monkeypatch.setattr(qa.semantic,'search',search);details={}
    hits=qa._retrieve('compare vocabulary training',[{'id':'a','title':'A'},{'id':'b','title':'B'}],6,details)
    assert calls==[{'a'},{'b'}] and details['mode']=='mixed'
    assert details['coverage']=={'a':3,'b':3} and len(hits)==6
    assert details['index_required'][0]['document_id']=='b'


def test_sparse_paper_does_not_waste_remaining_slots(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('only','vocabulary encoder decoder words')],
                              'b':[block('b'+str(i),'vocabulary encoder decoder words '+str(i)) for i in range(8)]})
    hits=qa._retrieve('vocabulary',[{'id':'a','title':'A'},{'id':'b','title':'B'}],6)
    assert len(hits)==6 and len({hit['id'] for hit in hits})==6
    assert sum(hit['document_id']=='a' for hit in hits)==1


def test_mixed_phase_passage_is_not_hidden_by_weak_phase_hint(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('target','We pre-train with a maximum sequence length of 771 and batch size 31 before fine-tuning.'),
                                  *[block(str(i),'Pre-training is used for the baseline model.') for i in range(9)]]})
    assert qa._retrieve('预训练的最大序列长度和批量大小是多少？',[{'id':'a','title':'Fixture'}],1)[0]['block_id']=='target'


def test_repeated_model_label_table_does_not_displace_requested_vocabulary(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('target','We use a vocabulary of 37000 wordpieces.'),
                                  block('table','F7-Small 23 F7-Base 35 F7-Large 44 F7-Huge 78 F7-XLarge 79')]})
    assert qa._retrieve('F7 的词表有多大？',[{'id':'a','title':'Fixture'}],1)[0]['block_id']=='target'


def test_split_sentence_gets_separate_neighbor_anchor_within_budget(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('start','We use a vocabulary of 37000', ['4.3 Vocabulary']),
                                  block('end','wordpieces. This vocabulary is shared across the input and output.', ['4.3 Vocabulary']),
                                  *[block(str(i),'Vocabulary sharing in other experiments is discussed here.') for i in range(5)]]})
    hits=qa._retrieve('词表是否共享？',[{'id':'a','title':'Fixture'}],3)
    assert len(hits)==3 and {'start','end'}<={hit['block_id'] for hit in hits}
    start=next(hit for hit in hits if hit['block_id']=='start')
    assert start['text']=='We use a vocabulary of 37000' and start['bbox']==[.1,.1,.8,.2]


def test_context_never_crosses_section_heading(monkeypatch):
    setup_sources(monkeypatch,{'a':[block('other','An unrelated unfinished phrase', ['3. Previous']),
                                  block('target','wordpieces vocabulary shared across input and output.', ['4. Current'])]})
    hits=qa._retrieve('词表是否共享？',[{'id':'a','title':'Fixture'}],1)
    assert hits[0]['block_id']=='target'


def test_context_does_not_expand_irrelevant_dense_hits_or_drop_matching_seed():
    def source(identity,text,order):
        return {'id':identity,'document_id':'a','parse_run_id':'r','source_order':order,
                'block_type':'paragraph','extraction':'native_pdf','section_path':['4. Methods'],
                'page':1,'text':text,'score':1,'bm25_score':1}
    irrelevant=[source(str(i),'unrelated incomplete text',i) for i in range(10)]
    first=source('start','The vocabulary contains 37000',10)
    last=source('end','wordpieces. The vocabulary is shared across the input and output.',11)
    ranked=[*irrelevant[:5],last]
    hits=qa._continuation_context(ranked,[*irrelevant,first,last],6,'词表是否共享？')
    assert len(hits)==6 and {'start','end'}<={hit['id'] for hit in hits}
    assert sum(hit.get('retrieval_context')=='adjacent_continuation' for hit in hits)==1


def test_real_pdf_superscript_preserves_raw_text_and_adds_bound_layout(tmp_path):
    pdf=pymupdf.open();page=pdf.new_page()
    page.insert_text((50,100),'Synthetic training tokens: 2',fontsize=12)
    x=50+pymupdf.get_text_length('Synthetic training tokens: 2',fontsize=12)
    page.insert_text((x,96),'17',fontsize=8)
    page.insert_text((x+10,100),' = 131072 tokens.',fontsize=12)
    values=parser._extract_raw(page)
    value=next(value for value in values if '131072' in value['text'])
    assert '217' in value['text'] and '2^{17}' in value['layout_text']
    assert value['rect'][0]==50
    source={'id':'ev','text':value['text'],'metadata':{'layout_text':value['layout_text'],
            'raw_text_sha256':hashlib.sha256(value['text'].encode()).hexdigest(),'layout_format':'superscript_braces_v1'}}
    assert '2^{17}' in qa.model_source_text(source)
    assert not qa._validate_claims({'claims':[{'text':'217 tokens','evidence_ids':['ev']}]},[source])
    assert qa._validate_claims({'claims':[{'text':'2^{17} tokens','evidence_ids':['ev']}]},[source])
    source['text']='Changed source';assert qa.model_source_text(source)=='Changed source'


def test_model_receives_layout_and_section_without_changing_anchor(monkeypatch):
    import asyncio
    from backend.app import model_client
    source={'id':'ev','title':'Synthetic fixture','page':2,'text':'217 tokens',
            'section_path':['4.2 Baseline'], 'metadata':{'layout_text':'2^{17} tokens',
             'raw_text_sha256':hashlib.sha256(b'217 tokens').hexdigest(),'layout_format':'superscript_braces_v1'}}
    async def generate(system,payload,**kwargs):
        assert payload['evidence'][0]['text']=='2^{17} tokens'
        assert payload['evidence'][0]['section_path']==['4.2 Baseline']
        return {'claims':[]}
    monkeypatch.setattr(model_client,'chat_json',generate)
    asyncio.run(qa._generate('How many tokens?',[source]));assert source['text']=='217 tokens'


@pytest.fixture
def paper(client,sample_pdfs):
    return wait_for_document(client,upload_pdf(client,sample_pdfs['distinct'])['id'])


def wait_index(client,identity):
    for _ in range(100):
        response=client.get('/api/semantic-index-jobs/'+identity);response.raise_for_status();job=response.json()
        if job['status'] not in {'queued','running'}:return job
        time.sleep(.02)
    pytest.fail('index did not finish')


def test_index_requires_prepared_model_and_valid_snapshot(client,paper,monkeypatch):
    monkeypatch.setattr(semantic_jobs.semantic,'runtime_status',lambda:{'ready':False,'enabled':True})
    body={'document_id':paper['id'],'parse_run_id':paper['parse_run_id']}
    assert client.post('/api/semantic-index-jobs',json=body).status_code==409
    assert client.post('/api/semantic-index-jobs',json={**body,'parse_run_id':'invalid'}).status_code==404


def test_explicit_index_progress_dedup_and_cached_completion(client,paper,monkeypatch):
    entered=threading.Event();release=threading.Event();calls=[]
    monkeypatch.setattr(semantic_jobs.semantic,'runtime_status',lambda:{'ready':True,'enabled':True})
    def search(question,sources,**kwargs):
        assert kwargs['force_index'];calls.append(sources);kwargs['progress'](2,5);entered.set()
        assert release.wait(3);kwargs['progress'](5,5)
        return [],{'used':True,'indexed_chunks':5}
    monkeypatch.setattr(semantic_jobs.semantic,'search',search)
    body={'document_id':paper['id'],'parse_run_id':paper['parse_run_id']}
    first=client.post('/api/semantic-index-jobs',json=body);assert first.status_code==202
    try:
        assert entered.wait(2)
        duplicate=client.post('/api/semantic-index-jobs',json=body).json()
        assert duplicate['id']==first.json()['id'] and duplicate['done']==2
    finally:release.set()
    done=wait_index(client,first.json()['id'])
    assert done['status']=='completed' and done['done']==5 and len(calls)==1
    assert all(source['parse_run_id']==paper['parse_run_id'] for source in calls[0])


def test_index_error_is_sanitized(client,paper,monkeypatch):
    monkeypatch.setattr(semantic_jobs.semantic,'runtime_status',lambda:{'ready':True,'enabled':True})
    def fail(*a,**k):raise RuntimeError('fictional-sensitive-provider-body')
    monkeypatch.setattr(semantic_jobs.semantic,'search',fail)
    result=client.post('/api/semantic-index-jobs',json={'document_id':paper['id'],'parse_run_id':paper['parse_run_id']})
    done=wait_index(client,result.json()['id'])
    assert done['status']=='failed' and 'fictional-sensitive' not in str(done)
