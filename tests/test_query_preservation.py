"""Synthetic retrieval contrasts, independent of paper identities/answer values."""
import copy
import pytest
from backend.app import qa,answer_fields

@pytest.mark.parametrize('text,expected',[
    ('dynamic mask-\ning',['mask','ing','masking']),
    ('a selected conﬁguration',['selected','configuration']),
    ('byte-\nlevel encoding',['byte','level','bytelevel']),
    ('token\u00ad\nization',['token','ization','tokenization']),
])
def test_token_forms_preserve_parts_and_normalize_pdf_line_breaks(text,expected):
    result=qa._tokens(text)
    assert all(term in result for term in expected)

def test_ordinary_hyphen_or_paragraph_break_does_not_join_words():
    assert 'endtask' not in qa._tokens('end-task')
    assert 'alphabeta' not in qa._tokens('alpha\nbeta')

def test_question_terms_survive_generic_field_expansion():
    query=answer_fields.probe_query('模型最终配置的QMASK策略是否采用ZXLOSS loss？','objective')
    assert 'QMASK' in query and 'ZXLOSS' in query and 'loss' in query

def test_negative_constraints_do_not_add_probe_terms():
    query=answer_fields.probe_query('QMASK的训练目标是什么？不要使用FORBIDDEN设置。','objective')
    assert 'QMASK' in query and 'FORBIDDEN' not in query

def test_probe_has_a_bound_on_explicit_latin_terms():
    words=['TERM'+str(i) for i in range(30)]
    query=answer_fields.probe_query(' '.join(words),'objective')
    assert 'TERM11' in query and 'TERM12' not in query

def test_final_configuration_concepts_are_generic():
    assert {'final','configuration','strategy'}<=set(qa._tokens('最终配置采用什么策略？',query=True))

def test_final_paragraph_with_ligature_and_wrapped_terms_is_retrievable(monkeypatch):
    blocks=[{'id':'target','page':3,'bbox':[.1,.2,.9,.4],'type':'paragraph',
             'text':'We call this conﬁguration the final setup. It uses QMASK mask-\ning and does not use ZXLOSS loss.'},
            *[{'id':'noise'+str(i),'page':2,'bbox':[.1,.2,.9,.4],'type':'paragraph',
               'text':'Training objective loss masked modeling symmetric cross entropy cosine.'} for i in range(10)]]
    original=copy.deepcopy(blocks)
    monkeypatch.setattr(qa.store,'get_structure',lambda *_a,**_k:{'parse_run_id':'run','blocks':blocks})
    monkeypatch.setattr(qa.semantic,'search',lambda *_a,**_k:([],{'used':False}))
    hits=qa._retrieve_question('最终配置采用什么QMASK策略，是否使用ZXLOSS loss？',[{'id':'d','title':'Synthetic'}],3)
    assert any(hit['block_id']=='target' for hit in hits)
    hit=next(hit for hit in hits if hit['block_id']=='target')
    assert hit['text']==blocks[0]['text'] and hit['bbox']==blocks[0]['bbox'] and blocks==original
